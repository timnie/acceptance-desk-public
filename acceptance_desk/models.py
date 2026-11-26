"""本地 NLI 与句向量模型（handoff §2.8，schema §5.1–§5.2）。

加载方式与 experiments/step1_smoke_test.py 一致：
- 只从本地目录加载（local_files_only），不联网、不用 trust_remote_code；
- NLI 只有 pytorch_model.bin，用 torch.load(weights_only=True) 读出后严格比对键名；
- 类别按 config.id2label 的名字取值，不按数组位置猜；
- BGE 取最后一层 [CLS]，L2 归一化后做点积；
- 首次加载时核对两个权重文件的 SHA256（与 Step 1 相同的固定值），每个目录每个进程只核对一次。
torch / transformers 在 load() 里才导入，规则模式不依赖它们。
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

ENV_CACHE = "ACCEPTANCE_DESK_MODEL_CACHE"
DEFAULT_CACHE = ".model-cache"

NLI_NAME = "IDEA-CCNL/Erlangshen-Roberta-110M-NLI"
NLI_REVISION = "864d25be3ce5e90d9193cb49d9fbd52722cdc6b0"
NLI_DIR = "erlangshen-nli"
EMB_NAME = "BAAI/bge-small-zh-v1.5"
EMB_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
EMB_DIR = "bge-small-zh-v1.5"

_NLI_LABELS = ("entailment", "neutral", "contradiction")

# 固定版本在 Hugging Face 官方仓库的 LFS SHA256，试跑命令也从此处读取。
EXPECTED_SHA256 = {
    f"{NLI_DIR}/pytorch_model.bin": "3ee7de12d0dc27d21a3e4a8713e270fa431102c20d74351b876d76f9c8a30b03",
    f"{EMB_DIR}/model.safetensors": "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026",
}
REQUIRED_FILES = (
    f"{NLI_DIR}/pytorch_model.bin", f"{NLI_DIR}/config.json", f"{NLI_DIR}/vocab.txt",
    f"{EMB_DIR}/model.safetensors", f"{EMB_DIR}/config.json", f"{EMB_DIR}/tokenizer.json",
)


class ModelUnavailable(Exception):
    """模型依赖未安装、权重缺失或版本不符。"""


def resolve_cache_dir(cache_dir: str | os.PathLike | None = None) -> Path:
    """优先级：参数 > 环境变量 ACCEPTANCE_DESK_MODEL_CACHE > 当前目录下的 .model-cache/。"""
    return Path(cache_dir or os.environ.get(ENV_CACHE) or DEFAULT_CACHE).expanduser().resolve()


def _check_revision(model_dir: Path, weight: str, expected: str) -> None:
    """huggingface_hub 下载时会留下 .cache/huggingface/download/<文件>.metadata，第一行是提交号。
    有这份记录就核对版本；手动放置的文件没有记录，则不核对。"""
    meta = model_dir / ".cache" / "huggingface" / "download" / f"{weight}.metadata"
    if not meta.exists():
        return
    try:
        lines = meta.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ModelUnavailable(f"无法读取下载记录 {meta}：{e}") from e
    got = lines[0].strip() if lines else ""
    if not got:
        raise ModelUnavailable(f"下载记录 {meta} 为空或格式不对，无法确认版本")
    if got != expected:
        raise ModelUnavailable(f"{model_dir.name} 的版本是 {got}，期望 {expected}")


def weight_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_files(cache: Path) -> None:
    """文件齐全、下载记录的版本一致、权重 SHA256 一致；任何一项不满足都抛 ModelUnavailable。"""
    for rel in REQUIRED_FILES:
        if not (cache / rel).is_file():
            raise ModelUnavailable(f"缺少模型文件：{cache / rel}")
    _check_revision(cache / NLI_DIR, "pytorch_model.bin", NLI_REVISION)
    _check_revision(cache / EMB_DIR, "model.safetensors", EMB_REVISION)
    for rel, expected in EXPECTED_SHA256.items():
        try:
            got = weight_sha256(cache / rel)
        except OSError as e:
            raise ModelUnavailable(f"无法读取权重 {cache / rel}：{e}") from e
        if got != expected:
            raise ModelUnavailable(f"权重 SHA256 不符（文件可能损坏或不是固定版本）：{rel}，期望 {expected}，实际 {got}")


def nli_probabilities(torch, tokenizer, model, label_index, premise: str, hypothesis: str) -> dict[str, float]:
    with torch.no_grad():
        enc = tokenizer(premise, hypothesis, return_tensors="pt", truncation=True, max_length=512)
        probs = torch.softmax(model(**enc).logits, dim=-1)[0]
        return {name: float(probs[index]) for name, index in label_index.items()}


def normalized_embeddings(torch, tokenizer, model, sentences: list[str]):
    with torch.no_grad():
        enc = tokenizer(sentences, padding=True, truncation=True, max_length=512, return_tensors="pt")
        cls = model(**enc).last_hidden_state[:, 0]
        return torch.nn.functional.normalize(cls, p=2, dim=1)


class FeatureModels:
    def __init__(self, nli_tokenizer, nli_model, label_index, emb_tokenizer, emb_model, torch_mod):
        self._nt, self._nm, self._label_index = nli_tokenizer, nli_model, label_index
        self._et, self._em = emb_tokenizer, emb_model
        self._torch = torch_mod
        self.info = {
            "nli": {"name": NLI_NAME, "revision": NLI_REVISION},
            "embedding": {"name": EMB_NAME, "revision": EMB_REVISION},
        }

    @classmethod
    def load(cls, cache_dir: str | os.PathLike | None = None) -> "FeatureModels":
        """失败抛 ModelUnavailable。同一目录只加载一次（进程内缓存）。"""
        return _load_cached(resolve_cache_dir(cache_dir))

    def nli(self, premise: str, hypothesis: str) -> dict[str, float]:
        probs = nli_probabilities(self._torch, self._nt, self._nm, self._label_index, premise, hypothesis)
        return {key: round(probs[key], 4) for key in _NLI_LABELS}

    def similarity(self, a: str, b: str) -> float:
        e = normalized_embeddings(self._torch, self._et, self._em, [a, b])
        return round(float(e[0] @ e[1]), 4)


@lru_cache(maxsize=4)
def _load_cached(cache: Path) -> FeatureModels:
    nli_dir, emb_dir = cache / NLI_DIR, cache / EMB_DIR
    verify_files(cache)

    try:
        import torch
        from . import _model_backend as backend
    except ImportError as e:
        raise ModelUnavailable(f"未安装模型依赖（pip install '.[models]'）：{e}") from e

    try:
        nt, nm = backend.load_nli(nli_dir)
        id2label = {int(i): name.lower() for i, name in nm.config.id2label.items()}
        if sorted(id2label.values()) != sorted(_NLI_LABELS):
            raise ModelUnavailable(f"NLI 的 id2label 不是预期的三类：{id2label}")
        label_index = {name: i for i, name in id2label.items()}

        et, em = backend.load_embedding(emb_dir)
    except backend.WeightMismatch as exc:
        raise ModelUnavailable(str(exc)) from exc
    except ModelUnavailable:
        raise
    except Exception as e:  # 文件损坏、版本不兼容等
        raise ModelUnavailable(f"模型加载失败：{type(e).__name__}: {e}") from e

    return FeatureModels(nt, nm, label_index, et, em, torch)
