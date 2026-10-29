"""两张近景、单答案词元以及语言模型 LoRA 的共用实现。"""
from __future__ import annotations
import random

import exp6m_common as c


def load_processor(model_path=c.MODEL):
    from transformers import AutoProcessor
    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
    processor.tokenizer.padding_side = 'left'
    tokens = {word: processor.tokenizer.encode(word, add_special_tokens=False) for word in ('yes', 'no')}
    if any(len(ids) != 1 for ids in tokens.values()) or tokens['yes'] == tokens['no']:
        raise ValueError(f'yes/no 必须是不同的单词元: {tokens}')
    return processor, {key: value[0] for key, value in tokens.items()}


def image_pair(row, rng=None):
    from PIL import Image
    pair = []
    for key in ('t1', 't2'):
        with Image.open(row['inputs'][key]) as im:
            pair.append(im.convert('RGB'))
    if rng is not None:
        for operation in (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_TOP_BOTTOM):
            if rng.random() < .5:
                pair = [im.transpose(operation) for im in pair]
        angle = rng.randrange(4)
        if angle:
            op = {1: Image.Transpose.ROTATE_90, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_270}[angle]
            pair = [im.transpose(op) for im in pair]
        if rng.random() < .5:
            pair.reverse()
    return pair


def messages(system, images):
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': [
        {'type': 'text', 'text': 'T1, earlier:'}, {'type': 'image', 'image': images[0]},
        {'type': 'text', 'text': 'T2, later:'}, {'type': 'image', 'image': images[1]},
        {'type': 'text', 'text': c.QUESTION}]}]


def pack(processor, tokens, rows, system, context_limit, *, answers=False, aug_seeds=None):
    import torch
    texts, images = [], []
    for i, row in enumerate(rows):
        pair = image_pair(row, random.Random(aug_seeds[i]) if aug_seeds is not None else None)
        text = processor.apply_chat_template(messages(system, pair), tokenize=False,
                                             add_generation_prompt=True, enable_thinking=False)
        if answers:
            answer = 'yes' if row['label'] else 'no'
            before = processor.tokenizer.encode(text, add_special_tokens=False)
            after = processor.tokenizer.encode(text + answer, add_special_tokens=False)
            if after != before + [tokens[answer]]:
                raise ValueError('实际 assistant 上下文中答案并非独立单词元')
        texts.append(text)
        images.extend(pair)
    batch = processor(text=texts, images=images, return_tensors='pt', padding=True, truncation=False)
    targets = torch.tensor([tokens['yes' if row['label'] else 'no'] for row in rows], dtype=torch.long)
    if answers:
        batch['input_ids'] = torch.cat([batch['input_ids'], targets[:, None]], dim=1)
        batch['attention_mask'] = torch.cat([batch['attention_mask'], torch.ones_like(targets[:, None])], dim=1)
        if 'mm_token_type_ids' in batch:
            batch['mm_token_type_ids'] = torch.cat([batch['mm_token_type_ids'], torch.zeros_like(targets[:, None])], dim=1)
        labels = torch.full_like(batch['input_ids'], -100)
        labels[:, -1] = targets
        if not torch.all((labels != -100).sum(dim=1) == 1):
            raise ValueError('答案掩码必须每条恰好一个词元')
        batch['labels'] = labels
    if batch['input_ids'].shape[1] > context_limit:
        raise ValueError(f'输入长度 {batch["input_ids"].shape[1]} 超过 {context_limit}，不截断')
    return batch, targets


def load_model(model_path, *, adapter=None, train=False, rank=16, alpha=32, dropout=.05, checkpointing=True):
    import torch
    from transformers import Qwen3_5ForConditionalGeneration
    from peft import LoraConfig, PeftModel, get_peft_model
    model = Qwen3_5ForConditionalGeneration.from_pretrained(
        str(model_path), local_files_only=True, dtype=torch.bfloat16, device_map={'': 'cuda:0'})
    model.requires_grad_(False)
    model.config.use_cache = False
    if adapter is None:
        leaves = {'q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'}
        targets = [name for name, module in model.named_modules()
                   if name.startswith('model.language_model.') and name.rsplit('.', 1)[-1] in leaves
                   and isinstance(module, torch.nn.Linear)]
        if not targets:
            raise ValueError('未找到语言模型 LoRA 目标层')
        model = get_peft_model(model, LoraConfig(r=rank, lora_alpha=alpha, lora_dropout=dropout,
                  target_modules=targets, bias='none', task_type='CAUSAL_LM'))
    else:
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=train)
    if train:
        if checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
            model.enable_input_require_grads()
        trainable = [name for name, p in model.named_parameters() if p.requires_grad]
        if not trainable or any('.language_model.' not in name or 'lora_' not in name for name in trainable):
            raise ValueError('可训练参数必须仅为语言模型 LoRA')
        model.train()
        # 训练模式下视觉塔仍保持 eval；梯度与权重均冻结。
        model.get_base_model().model.visual.eval()
    else:
        model.eval()
    return model


def answer_loss(model, batch):
    """仅计算最后一个答案位置的完整词表 CE，与其余 labels=-100 等价。"""
    import torch.nn.functional as F
    labels = batch.pop('labels')
    logits = model(**batch, use_cache=False, logits_to_keep=2).logits[:, -2, :].float()
    return F.cross_entropy(logits, labels[:, -1])


def score_batch(model, processor, tokens, data, system, context_limit):
    import torch
    import torch.nn.functional as F
    batch, targets = pack(processor, tokens, data, system, context_limit)
    batch = batch.to('cuda:0')
    with torch.inference_mode():
        logits = model(**batch, use_cache=False, logits_to_keep=1).logits[:, -1, :].float()
        probability = logits[:, [tokens['no'], tokens['yes']]].softmax(-1)[:, 1]
        loss = F.cross_entropy(logits, targets.to('cuda:0'), reduction='none')
    return probability.cpu().tolist(), loss.cpu().tolist(), int(batch['input_ids'].shape[1])


def dry_check(processor, tokens, data, system, context_limit):
    import torch
    probes = []
    # 真实图像同时检查 yes/no 两种答案的编码，标签变体仅用于词元检查。
    for answer in (0, 1):
        row = dict(data[0], label=answer)
        batch, targets = pack(processor, tokens, [row], system, context_limit, answers=True)
        before, _ = pack(processor, tokens, [row], system, context_limit)
        assert torch.equal(batch['input_ids'][:, :-1], before['input_ids'])
        assert int((batch['labels'] != -100).sum()) == 1
        assert int(batch['labels'][0, -1]) == int(targets[0])
        probes.append(dict(answer='yes' if answer else 'no', token_id=int(targets[0]),
                           input_tokens=int(batch['input_ids'].shape[1]), supervised_tokens=1,
                           image_grid_thw=batch['image_grid_thw'].tolist()))
    return dict(model_loaded=False, tokenizer=type(processor.tokenizer).__name__,
                tokens=tokens, question=c.QUESTION, probes=probes)
