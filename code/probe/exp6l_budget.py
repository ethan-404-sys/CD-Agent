"""Recompute revised single-seed form-A budgets from actual half manifests; no inference."""
from argparse import ArgumentParser, Namespace
import exp6l_common as c


def config(ds, variant, k, model='api'):
    return c.configuration(Namespace(dataset=ds, variant=variant, k=k, form='A', bq=False,
        final=False, frozen=None, prepared=c.HERE/'run/prepared', limit=0, dry_run=True), model)


def main():
    p = ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true', required=True)
    a = p.parse_args()
    api = []
    def add(conf, stage):
        gs = c.groups(conf)
        values = [c.estimate(conf, group) for group in gs]
        api.append(dict(dataset=conf['dataset'], stage=stage, k=conf['k'], bq=conf['bq'],
                        calls=len(gs), candidates=sum(map(len, gs)),
                        **{key: sum(v[key] for v in values) for key in values[0]}))
    for ds in ('sysu', 'second'):
        for variant, k in [('Z0', 0), ('Z1', 0), *[('F', k) for k in c.KS[1:]]]:
            add(config(ds, variant, k), variant)
        conf = config(ds, 'F', 16)
        add(dict(conf, bq=True), 'BQ_placeholder')
        add(dict(conf, final=True), 'test_placeholder')
        for k in c.KS:
            conf = config(ds, 'F' if k else 'Z1', k)
            add(dict(conf, bq=True), 'BQ_sensitivity')
            add(dict(conf, final=True), 'test_sensitivity')
            add(dict(conf, bq=True, final=True), 'test_BQ_sensitivity')
    old = c.read(c.HERE / 'estimate_evidence.json')
    local = []
    for model in ('qwen', 'dars4b', 'earthdial'):
        for ds in ('sysu', 'second'):
            lengths = old['local_lengths'][f'{model}/{ds}']
            near = old['timings'][f'{model}/{ds}']['near']
            for variant, k in [('Z0', 0), ('Z1', 0), *[('F', k) for k in c.KS[1:]]]:
                length = near['input_tokens'] if variant == 'Z0' else lengths['base']+k*lengths['demo']
                seconds = near['seconds']+(length-near['input_tokens'])*lengths['seconds_per_added_token']
                local.append(dict(model=model, dataset=ds, variant=variant, k=k,
                    approximate_input_tokens=length, default_limit=262144 if model=='qwen' else 8192 if model=='dars4b' else 4096,
                    seconds=seconds, dev_inference_hours=600*seconds/3600 if model!='earthdial' else None,
                    test_inference_hours=1500*seconds/3600,
                    load_hours=old['gpu_totals'][model]['one_load_hours']))
    totals = {}
    for model in ('qwen', 'dars4b'):
        values = [r for r in local if r['model']==model]
        infer = sum(r['dev_inference_hours'] for r in values)
        loads = sum(r['load_hours'] for r in values)
        totals[model] = dict(dev_inference_hours=infer, loads=12, load_hours=loads,
                            dev_hours_with_per_run_load=infer+loads,
                            test_k16_hours=sum(r['test_inference_hours']+r['load_hours'] for r in values if r['k']==16))
    plan = [r for r in api if not r['stage'].endswith('sensitivity')]
    report = dict(api=api, local=local, local_totals=totals,
                  api_total={key: sum(r[key] for r in plan) for key in ('calls','candidates','input_tokens','output_tokens','cost')},
                  assumptions=['BQ and test use k16/A placeholders; choose settings on development AP',
                               '48 output tokens/candidate; no cached input discount',
                               'GPU slopes/lengths from previous ESTIMATE evidence; actual multiround context checked at runtime',
                               '12 independent model loads per local development model; sharding adds loads',
                               'Projected DA-RS k12/16 times assume explicit context extension succeeds; default may fail'])
    c.write(c.HERE/'run/budget_revised.json', report)
    print(report['api_total'])
    print(totals)


if __name__ == '__main__':
    main()
