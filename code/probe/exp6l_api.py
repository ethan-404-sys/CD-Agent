"""Bailian OpenAI-compatible inference; dry-run never reads credentials or sends requests."""
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

import exp6l_common as c


class BudgetStopped(RuntimeError):
    pass


class RequestFailed(RuntimeError):
    def __init__(self, kind, retryable=False, unsupported_logprobs=False):
        super().__init__(kind)
        self.retryable = retryable
        self.unsupported_logprobs = unsupported_logprobs


class Budget:
    """Durable, process-shared reservations; unknown charges remain reserved after a crash."""
    def __init__(self, path, maximum):
        self.path, self.maximum = c.local(path), maximum
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.Lock()

    def update(self, mutate):
        with self.mutex, self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = c.read(self.path) if self.path.exists() else dict(max_cny=self.maximum, attempts=[])
            if data['max_cny'] != self.maximum:
                raise ValueError('Shared budget limit differs from --max-cny')
            value = mutate(data)
            c.write(self.path, data)
            return value

    def reserve(self, key, maximum, attempts):
        def mutate(data):
            prior = [r for r in data['attempts'] if r['key'] == key]
            if len(prior) >= attempts:
                raise RequestFailed('attempt_limit')
            charged = sum(r.get('cost', r['reserved']) for r in data['attempts'])
            if charged + maximum > self.maximum + 1e-12:
                raise BudgetStopped('budget_reservation_limit')
            token = uuid.uuid4().hex
            data['attempts'].append(dict(id=token, key=key, reserved=maximum, attempt=len(prior)+1))
            return token
        return self.update(mutate)

    def settle(self, token, usage):
        def mutate(data):
            row = next(r for r in data['attempts'] if r['id'] == token)
            if usage['cost'] > row['reserved'] + 1e-12:
                raise RuntimeError('Provider usage exceeds reserved model limits')
            row.update(cost=usage['cost'], usage=usage)
        self.update(mutate)


def api_messages(messages):
    out = []
    for msg in messages:
        content = msg['content']
        if isinstance(content, list):
            converted = []
            for part in content:
                if part['type'] == 'text':
                    converted.append(dict(type='text', text=part['text']))
                else:
                    path = Path(part['path'])
                    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
                    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
                    converted.append(dict(type='image_url', image_url=dict(url=f'data:{mime};base64,{encoded}')))
            content = converted
        out.append(dict(role=msg['role'], content=content))
    return out


def request(body, endpoint, key):
    req = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions',
        data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        # Inspect only for the documented optional-parameter fallback; never persist server text.
        detail = error.read(4096).decode('utf-8', 'replace').lower()
        unsupported = error.code == 400 and 'logprob' in detail and any(
            word in detail for word in ('not support', 'unsupported', 'not allowed'))
        raise RequestFailed(f'http_{error.code}', error.code in (429, 500, 502, 503, 504), unsupported) from None
    except json.JSONDecodeError:
        raise RequestFailed('invalid_response') from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise RequestFailed('transport_error', retryable=True) from None


def read_usage(response):
    raw = response.get('usage') or {}
    if 'prompt_tokens' not in raw or 'completion_tokens' not in raw:
        raise RequestFailed('missing_usage')
    n, out = int(raw['prompt_tokens']), int(raw['completion_tokens'])
    cached = int((raw.get('prompt_tokens_details') or {}).get('cached_tokens') or 0)
    if not (0 <= cached <= n <= 260096 and 0 <= out <= 32768):
        raise RequestFailed('invalid_usage')
    return dict(input_tokens=n, output_tokens=out, cached_tokens=cached, cost=c.cost(n, out, cached))


def answer_probabilities(choice, raw, count):
    """Locate change values in parsed JSON, then align their character positions to tokens."""
    entries = (choice.get('logprobs') or {}).get('content') or []
    if ''.join(e['token'] for e in entries) != raw:
        return [None] * count
    # JSON parsing is performed by parse_response; this only locates the answer token offsets.
    positions = [m.end() for m in re.finditer(r'"change"\s*:\s*"', raw)]
    if len(positions) != count:
        return [None] * count
    results, offset = [], 0
    spans = []
    for entry in entries:
        spans.append((offset, offset + len(entry['token']), entry))
        offset += len(entry['token'])
    for pos in positions:
        entry = next((e for lo, hi, e in spans if lo <= pos < hi), None)
        mass = {}
        if entry is not None and entry['token'].strip().strip('"').lower() in ('yes', 'no'):
            for alt in entry.get('top_logprobs') or []:
                word = alt['token'].strip().strip('"').lower()
                if word in ('yes', 'no') and math.isfinite(alt['logprob']):
                    mass[word] = mass.get(word, 0.) + math.exp(alt['logprob'])
        results.append(dict(p_yes=mass['yes'] / sum(mass.values()), yes_no_mass=sum(mass.values()))
                       if set(mass) == {'yes', 'no'} and sum(mass.values()) > 0 else None)
    return results


def parse_response(response, group, batched):
    try:
        choice = response['choices'][0]
        raw = choice['message']['content']
        if choice.get('finish_reason') != 'stop':
            raise ValueError('non-stop finish')
        parsed = json.loads(raw)
        answers = parsed['answers'] if batched else [parsed]
        if len(answers) != len(group):
            raise ValueError('wrong answer count')
        probs = answer_probabilities(choice, raw, len(group))
        results = []
        for row, answer, probability in zip(group, answers, probs, strict=True):
            if batched and answer['cid'] != row['cid']:
                raise ValueError('wrong candidate order')
            if answer['change'] not in ('yes', 'no') or not isinstance(answer['reason'], str):
                raise ValueError('invalid answer')
            confidence = answer['confidence']
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
                raise ValueError('invalid confidence')
            result = dict(cid=row['cid'], answer=answer['change'], confidence=confidence,
                          reason=answer['reason'], readout='token_prob' if probability else 'verbal_confidence')
            if probability:
                result.update(probability)
            results.append(result)
        return raw, results
    except (KeyError, IndexError, TypeError, ValueError):
        raise RequestFailed('invalid_response') from None


def infer(config, group, args, budget, endpoint, key, logprobs_enabled):
    group_key = group[0]['image'] if config['bq'] else group[0]['cid']
    attempt_key = str(c.local(args.out)) + ':' + group_key
    output_limit = min(32768, args.max_output_per_candidate * len(group) + (16 if config['bq'] else 0))
    body = dict(model='qwen3-vl-plus', messages=api_messages(c.messages(config, group)),
                response_format={'type': 'json_object'}, max_completion_tokens=output_limit,
                temperature=0, seed=c.SEED, enable_thinking=False, stream=False)
    started = time.monotonic()
    # Reserve the full legal input at the highest tier, independent of heuristic token estimates.
    reservation = (260096 * 3 + output_limit * 30) / 1e6
    for attempt in range(args.max_attempts):
        wants_logprobs = logprobs_enabled.is_set()
        if wants_logprobs:
            body.update(logprobs=True, top_logprobs=5)
        else:
            body.pop('logprobs', None)
            body.pop('top_logprobs', None)
        try:
            token = budget.reserve(attempt_key, reservation, args.max_attempts)
        except BudgetStopped:
            return dict(status='budget_stop', group_key=group_key, cids=[r['cid'] for r in group])
        except RequestFailed as exc:
            return dict(status='failed', group_key=group_key, cids=[r['cid'] for r in group], error=str(exc))
        attempt_path = c.local(args.out) / ('attempt-' + token + '.json')
        usage = None
        try:
            response = request(body, endpoint, key)
            usage = read_usage(response)
            budget.settle(token, usage)
            raw, results = parse_response(response, group, config['bq'])
            c.write(attempt_path, dict(group_key=group_key, status='ok', usage=usage))
            return dict(status='ok', group_key=group_key, cids=[r['cid'] for r in group],
                        results=results, raw_text=raw, usage=usage,
                        seconds=time.monotonic()-started, logprobs_requested=wants_logprobs)
        except RequestFailed as exc:
            c.write(attempt_path, dict(group_key=group_key, status='failed', error=str(exc), usage=usage,
                                      reserved_cny=reservation if usage is None else 0))
            if exc.unsupported_logprobs and wants_logprobs:
                logprobs_enabled.clear()
                c.write(c.local(args.out) / 'readout_state.json', dict(logprobs=False))
            elif not exc.retryable and str(exc) != 'invalid_response':
                return dict(status='failed', group_key=group_key, cids=[r['cid'] for r in group],
                            error=str(exc), fatal=True, seconds=time.monotonic()-started)
            if attempt + 1 < args.max_attempts:
                time.sleep(min(2 ** attempt, 8))
    return dict(status='failed', group_key=group_key, cids=[r['cid'] for r in group], error='attempt_limit')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    c.add_arguments(p)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--max-attempts', type=int, default=3)
    p.add_argument('--max-cny', type=float)
    p.add_argument('--budget-ledger', type=Path, default=c.HERE / 'run/api_budget.json')
    p.add_argument('--max-output-per-candidate', type=int, default=128)
    p.add_argument('--smoke', type=int, default=0, help='real calls on N evenly spaced groups; out must be under verification/')
    a = p.parse_args()
    if a.smoke and 'verification' not in c.local(a.out).parts:
        p.error('--smoke output must be under verification/')
    config = c.configuration(a, 'api')
    config.update(max_output_per_candidate=a.max_output_per_candidate, max_attempts=a.max_attempts,
                  budget_ledger=str(c.local(a.budget_ledger)))
    lock = c.initialize(a.out, config)
    if a.dry_run:
        c.dry_run(config, c.local(a.out), a.limit)
        return
    if not os.environ.get('SLURM_JOB_ID'):
        p.error('Formal inference must run in a Slurm job')
    if a.max_cny is None or not math.isfinite(a.max_cny) or a.max_cny <= 0:
        p.error('--max-cny must be a finite positive hard limit')
    if a.workers < 1 or a.max_attempts < 1 or not 1 <= a.max_output_per_candidate <= 32768:
        p.error('Invalid workers, attempts or output limit')
    c.claim_final(config, a.out)
    endpoint, key = os.environ['DASHSCOPE_BASE_URL'], os.environ['DASHSCOPE_API_KEY']
    budget = Budget(a.budget_ledger, a.max_cny)
    path = c.local(a.out) / 'predictions.jsonl'
    prior = c.journal(path)
    done = {cid for r in prior if r['status'] == 'ok' for cid in r['cids']}
    gs = c.groups(config)
    if a.smoke:
        gs = gs[::max(1, len(gs) // a.smoke)][:a.smoke]
    for group in gs:
        n = sum(r['cid'] in done for r in group)
        if n not in (0, len(group)):
            raise ValueError('Partial BQ group in resume journal')
    pending = iter(g for g in gs if g[0]['cid'] not in done)
    flag = threading.Event()
    state_path = c.local(a.out) / 'readout_state.json'
    if not state_path.exists() or c.read(state_path)['logprobs']:
        flag.set()
    stopped, failures = False, 0
    with ThreadPoolExecutor(a.workers) as pool:
        futures = set()
        def fill():
            while not stopped and len(futures) < a.workers:
                group = next(pending, None)
                if group is None:
                    break
                futures.add(pool.submit(infer, config, group, a, budget, endpoint, key, flag))
        fill()
        while futures:
            ready, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in ready:
                result = future.result()
                c.append(path, result)
                stopped |= result['status'] == 'budget_stop' or result.get('fatal', False)
                failures += result['status'] == 'failed'
            fill()
    records = c.journal(path)
    successful = {cid for r in records if r['status'] == 'ok' for cid in r['cids']}
    complete = len(successful) == sum(map(len, gs))
    c.write(c.local(a.out) / 'status.json', dict(complete=complete, completed=len(successful),
        budget_stopped=any(r['status'] == 'budget_stop' for r in records),
        fatal_error=any(r.get('fatal') for r in records), failures=failures))
    if not complete:
        raise SystemExit(2)
    lock.close()


if __name__ == '__main__':
    main()
