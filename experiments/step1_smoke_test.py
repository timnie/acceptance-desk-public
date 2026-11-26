"""Step 1：两个现成模型的本地加载与试跑。

用法（在仓库根目录）：
    .venv/bin/python experiments/step1_smoke_test.py
    .venv/bin/python experiments/step1_smoke_test.py --cache-dir 其他路径 --skip-sha

只从本地目录加载（local_files_only），不联网、不下载、不用 trust_remote_code。
- NLI：IDEA-CCNL/Erlangshen-Roberta-110M-NLI@864d25be，只有 pytorch_model.bin，
  这里显式用 torch.load(weights_only=True) 读权重，再严格 load_state_dict。
- 句向量：BAAI/bge-small-zh-v1.5@7999e1d3，按官方 README：取 [CLS] 的最后一层隐状态，
  再做 L2 归一化；句子对句子的相似度不加检索指令。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(ROOT))

from acceptance_desk import _model_backend as backend
from acceptance_desk.models import (EMB_DIR as BGE_DIR, EXPECTED_SHA256, NLI_DIR, nli_probabilities,
                                    normalized_embeddings, weight_sha256 as sha256)

DEFAULT_CACHE = ROOT / ".model-cache"

NLI_LABELS = ("CONTRADICTION", "NEUTRAL", "ENTAILMENT")
LABEL_ZH = {"ENTAILMENT": "蕴含", "NEUTRAL": "中立", "CONTRADICTION": "矛盾"}

# 三组自编句子：(组名, [(原句, 改句), ...])
CASES = [
    ("意思相近", [
        ("本研究采用问卷调查的方法收集了三所高校学生的数据。",
         "本研究通过问卷调查，收集了三所高校学生的数据。"),
        ("实验结果表明，新方法在小样本条件下的准确率高于基线方法。",
         "实验结果显示，在小样本条件下，新方法的准确率比基线方法更高。"),
    ]),
    ("明显矛盾", [
        ("实验结果表明，新方法在小样本条件下的准确率高于基线方法。",
         "实验结果表明，新方法在小样本条件下的准确率低于基线方法。"),
        ("受访者普遍支持延长图书馆的开放时间。",
         "受访者普遍反对延长图书馆的开放时间。"),
    ]),
    ("删去“可能”使论证变强", [
        ("睡眠不足可能是导致学生成绩下降的原因之一。",
         "睡眠不足是导致学生成绩下降的原因之一。"),
        ("这一结果可能说明政策干预对就业率有正向影响。",
         "这一结果说明政策干预对就业率有正向影响。"),
    ]),
]


def check_files(cache: Path) -> None:
    for rel, expected in EXPECTED_SHA256.items():
        p = cache / rel
        if not p.is_file():
            sys.exit(f"[失败] 找不到权重文件：{p}")
        got = sha256(p)
        if got != expected:
            sys.exit(f"[失败] SHA256 不一致：{rel}\n  期望 {expected}\n  实际 {got}")
        print(f"[通过] SHA256 {rel}")


def load_nli(path: Path):
    try:
        tokenizer, model = backend.load_nli(path)
    except backend.WeightMismatch as exc:
        sys.exit(f"[失败] NLI 权重与结构不匹配\n  缺少 {exc.missing}\n  多余 {exc.unexpected}")

    id2label = {int(i): name.upper() for i, name in model.config.id2label.items()}
    if sorted(id2label.values()) != sorted(NLI_LABELS):
        sys.exit(f"[失败] id2label 不是预期的三类：{id2label}")
    label_index = {name: i for i, name in id2label.items()}
    print(f"[通过] NLI 加载完成，id2label = {id2label}")
    return tokenizer, model, label_index


def nli_probs(tokenizer, model, label_index, premise: str, hypothesis: str) -> dict[str, float]:
    return nli_probabilities(torch, tokenizer, model, label_index, premise, hypothesis)


def load_bge(path: Path):
    tokenizer, model = backend.load_embedding(path)
    print(f"[通过] BGE 加载完成，hidden_size = {model.config.hidden_size}")
    return tokenizer, model


def bge_embed(tokenizer, model, sentences: list[str]) -> torch.Tensor:
    return normalized_embeddings(torch, tokenizer, model, sentences)


def sanity_check_labels(nli) -> None:
    """行为核对：同一句话对自己应判为蕴含，确认标签名和语义对得上。"""
    s = "本研究采用问卷调查的方法收集数据。"
    p = nli_probs(*nli, s, s)
    top = max(p, key=p.get)
    if top != "ENTAILMENT":
        sys.exit(f"[失败] 同句对的最高类别是 {top}，标签顺序可疑：{p}")
    print(f"[通过] 行为核对：同句对 ENTAILMENT={p['ENTAILMENT']:.3f}")


def fmt(p: dict[str, float]) -> str:
    return "  ".join(f"{LABEL_ZH[k]} {p[k]:.3f}" for k in ("ENTAILMENT", "NEUTRAL", "CONTRADICTION"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--skip-sha", action="store_true", help="跳过权重 SHA256 校验")
    args = ap.parse_args()
    cache = args.cache_dir.resolve()

    torch.manual_seed(0)
    print(f"torch {torch.__version__}  缓存目录 {cache}\n")
    if not args.skip_sha:
        check_files(cache)
    nli = load_nli(cache / NLI_DIR)
    bge = load_bge(cache / BGE_DIR)
    sanity_check_labels(nli)

    for group, pairs in CASES:
        print(f"\n===== {group} =====")
        for orig, rev in pairs:
            fwd = nli_probs(*nli, orig, rev)   # 前提=原句，假设=改句
            bwd = nli_probs(*nli, rev, orig)   # 反向：前提=改句，假设=原句
            e = bge_embed(*bge, [orig, rev])
            cos = float(e[0] @ e[1])
            print(f"原句：{orig}")
            print(f"改句：{rev}")
            print(f"  NLI 原→改：{fmt(fwd)}")
            print(f"  NLI 改→原：{fmt(bwd)}")
            print(f"  BGE 余弦相似度：{cos:.4f}\n")


if __name__ == "__main__":
    main()
