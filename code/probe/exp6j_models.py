"""Mini-InternVL2-DA-RS: separate images, exp3 readout, offline bf16 on one GPU."""
from __future__ import annotations

import importlib
import fcntl
import json
from pathlib import Path

from exp6j_common import FRAMING, PREFIX, HERE, readout, token_info
ROOT = HERE


def overlay(weights, code_from):
    """Weight dir without remote code (EarthDial): link its files plus the .py files of a same-architecture model."""
    target = ROOT / 'model_overlay' / weights.name
    target.mkdir(parents=True, exist_ok=True)
    sources = [f for f in weights.iterdir() if f.is_file()]
    sources += [f for f in code_from.glob('*.py') if not (weights / f.name).exists()]
    with (target / 'overlay.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for f in sources:
            link = target / f.name
            if not link.is_symlink():
                link.symlink_to(f.resolve())
            elif link.resolve() != f.resolve():
                raise ValueError(f'{link} points to {link.resolve()}, expected {f}')
    return target


def weight_keys(path):
    index = path / 'model.safetensors.index.json'
    if index.exists():
        return set(json.loads(index.read_text())['weight_map'])
    from safetensors import safe_open
    with safe_open(str(path / 'model.safetensors'), 'pt') as handle:
        return set(handle.keys())


def dynamic_preprocess(image, min_num=1, max_num=12, image_size=448, use_thumbnail=True):
    """Model-card tiling, including its area tie-break and PIL default resize."""
    width, height = image.size
    ratios = set((i, j) for n in range(min_num, max_num + 1)
                 for i in range(1, n + 1) for j in range(1, n + 1)
                 if min_num <= i * j <= max_num)
    ratios = sorted(ratios, key=lambda x: x[0] * x[1])
    best, difference = (1, 1), float('inf')
    for ratio in ratios:
        delta = abs(width / height - ratio[0] / ratio[1])
        if delta < difference or (delta == difference and
                width * height > 0.5 * image_size * image_size * ratio[0] * ratio[1]):
            best, difference = ratio, delta
    nx, ny = best
    resized = image.resize((image_size * nx, image_size * ny))
    crops = [resized.crop(((i % nx) * image_size, (i // nx) * image_size,
                           (i % nx + 1) * image_size, (i // nx + 1) * image_size))
             for i in range(nx * ny)]
    if use_thumbnail and len(crops) != 1:
        crops.append(image.resize((image_size, image_size)))
    return crops


class InternVL:
    def __init__(self, path, code_from=None):
        import torch
        import transformers
        from torchvision import transforms as T
        from torchvision.transforms import InterpolationMode

        self.torch = torch
        weights = Path(path)
        if code_from is not None:
            path = overlay(weights, Path(code_from))
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=True,
            use_fast=(weights / 'tokenizer.json').exists(), fix_mistral_regex=False)
        self.model = transformers.AutoModel.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=True, torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True, device_map={'': 'cuda:0'}, use_flash_attn=False).eval()
        stored, expected = weight_keys(weights), set(self.model.state_dict())
        missing, unexpected = sorted(expected - stored), sorted(stored - expected)
        if missing or unexpected:
            raise ValueError(f'Weight keys differ: missing {missing[:5]} ({len(missing)}), '
                             f'unexpected {unexpected[:5]} ({len(unexpected)})')
        self.llm = self.model.language_model
        self.config = self.model.config
        self.tokens = token_info(self.tokenizer)
        self.size = self.config.force_image_size or self.config.vision_config.image_size
        if self.size != 448:
            raise ValueError(f'Expected the 448px InternVL model, got {self.size}')
        # Tiling limits come from each model's config (DA-RS 12, EarthDial 6).
        self.max_num = self.config.max_dynamic_patch
        self.use_thumbnail = self.config.use_thumbnail
        self.transform = T.Compose([
            T.Resize((448, 448), interpolation=InterpolationMode.BICUBIC), T.ToTensor(),
            T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
        ])
        module = importlib.import_module(self.model.__class__.__module__)
        self.get_template = module.get_conv_template
        self.image_token = self.tokenizer.convert_tokens_to_ids('<IMG_CONTEXT>')
        if self.image_token is None or self.image_token == self.tokenizer.unk_token_id:
            raise ValueError('Missing <IMG_CONTEXT> token')
        self.model.img_context_token_id = self.image_token
        self.metadata = dict(backend='internvl', model=str(weights.resolve()),
                             code_from=str(Path(code_from).resolve()) if code_from else None,
                             weight_keys_checked=len(stored),
                             transformers=transformers.__version__, torch=torch.__version__,
                             language_model=self.llm.__class__.__name__, template=self.config.template,
                             generation='forced_prefix_forward_only',
                             native_generate=callable(getattr(self.llm, 'generate', None)),
                             attention='eager (use_flash_attn=False)', dtype='bfloat16',
                             image_size=448, max_num=self.max_num, use_thumbnail=self.use_thumbnail,
                             tokens=self.tokens, tokenizer_class=self.tokenizer.__class__.__name__)

    def inputs(self, features, patches, prompt, prefix=''):
        torch = self.torch
        labels = FRAMING[:len(patches)] if len(patches) > 1 else ('Panel sheet (numbered images):',)
        question = '\n'.join(f'{label}\n<image>' for label in labels) + '\n' + prompt
        conv = self.get_template(self.config.template)
        conv.system_message = self.model.system_message
        conv.append_message(conv.roles[0], question)
        conv.append_message(conv.roles[1], None)
        text = conv.get_prompt()
        for count in patches:
            replacement = '<img>' + '<IMG_CONTEXT>' * (self.model.num_image_token * count) + '</img>'
            text = text.replace('<image>', replacement, 1)
        batch = self.tokenizer(text + prefix, return_tensors='pt').to('cuda:0')
        embeddings = self.llm.get_input_embeddings()(batch['input_ids']).clone()
        selected = batch['input_ids'] == self.image_token
        visual = features.reshape(-1, embeddings.shape[-1]).to(embeddings.dtype)
        if int(selected.sum()) != visual.shape[0]:
            raise ValueError('Image token slots and visual features differ')
        embeddings[selected] = visual
        positions = torch.arange(embeddings.shape[1], device=embeddings.device).unsqueeze(0)
        return dict(inputs_embeds=embeddings, attention_mask=batch['attention_mask'], position_ids=positions), conv

    def score(self, images, prompt):
        torch = self.torch
        with torch.inference_mode():
            tiles = [dynamic_preprocess(im, max_num=self.max_num, use_thumbnail=self.use_thumbnail) for im in images]
            patches = [len(t) for t in tiles]
            pixels = torch.stack([self.transform(im) for group in tiles for im in group]).to(
                device='cuda:0', dtype=torch.bfloat16)
            features = self.model.extract_feature(pixels)
            batch, _ = self.inputs(features, patches, prompt, PREFIX)
            output = self.llm(**batch, use_cache=False, return_dict=True)
            result = readout(output.logits[0, -1], self.tokens)
            del output, batch
        torch.cuda.synchronize()
        return dict(**result, num_patches_list=patches)

    def peak_memory(self):
        return self.torch.cuda.max_memory_allocated() / 1024 ** 3



class Qwen:
    def __init__(self, path):
        import torch
        import transformers
        self.torch = torch
        self.processor = transformers.AutoProcessor.from_pretrained(str(path), local_files_only=True)
        self.model = transformers.Qwen3_5ForConditionalGeneration.from_pretrained(
            str(path), local_files_only=True, dtype=torch.bfloat16,
            device_map={'': 'cuda:0'}).eval()
        self.tokenizer = self.processor.tokenizer
        self.tokens = token_info(self.tokenizer)
        self.metadata = dict(backend='qwen', model=str(path), thinking=False,
                             transformers=transformers.__version__, torch=torch.__version__,
                             dtype='bfloat16', tokens=self.tokens)

    def score(self, images, prompt):
        labels = FRAMING[:len(images)] if len(images) > 1 else ('Panel sheet (numbered images):',)
        content = []
        for label, im in zip(labels, images):
            content.extend([{'type': 'text', 'text': label}, {'type': 'image', 'image': im}])
        content.append({'type': 'text', 'text': prompt})
        text = self.processor.apply_chat_template([{'role': 'user', 'content': content}],
                  tokenize=False, add_generation_prompt=True, enable_thinking=False) + PREFIX
        with self.torch.inference_mode():
            batch = self.processor(text=[text], images=images, return_tensors='pt').to('cuda:0')
            logits = self.model(**batch, use_cache=False, logits_to_keep=1).logits[0, -1]
            result = readout(logits, self.tokens)
        self.torch.cuda.synchronize()
        return dict(**result, image_grid_thw=batch['image_grid_thw'].tolist())

    def peak_memory(self):
        return self.torch.cuda.max_memory_allocated() / 1024 ** 3


def load_model(name):
    from exp6j_common import MODEL_PATHS
    if name == 'qwen':
        return Qwen(MODEL_PATHS[name])
    return InternVL(MODEL_PATHS[name], MODEL_PATHS['dars4b'] if name == 'earthdial' else None)
