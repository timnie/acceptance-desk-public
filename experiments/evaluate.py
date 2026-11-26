"""#8 通用二分类评测：计数、指标、Wilson 区间及每篇推送量。

输入是真实参考答案与系统预测，1 表示需要审阅。这里只计算，不选阈值、不训练。
CSV 必须恰好有 document_id,change_id,needs_review,predicted_review 四列，评测需提供独立清单。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from numbers import Integral
from pathlib import Path
from statistics import NormalDist


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float] | None:
    """二项比例的 Wilson score 区间；无分母时没有区间。"""
    if any(not isinstance(v, Integral) or isinstance(v, bool) for v in (successes, total)):
        raise ValueError("successes 和 total 必须是非 bool 整数")
    if not 0 <= successes <= total:
        raise ValueError("应满足 0 <= successes <= total")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence 必须在 0 和 1 之间")
    if total == 0:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p, z2 = successes / total, z * z
    scale = 1 + z2 / total
    center = (p + z2 / (2 * total)) / scale
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / scale
    # successes 为 0 或 total 时端点在数学上恰为 0 或 1；浮点舍入可能给出 2.8e-17 这类越界值，直接取精确端点。
    lower = 0.0 if successes == 0 else max(0.0, center - half)
    upper = 1.0 if successes == total else min(1.0, center + half)
    return lower, upper


def _ratio(numerator: int, denominator: int, intervals: bool) -> dict:
    bounds = wilson_interval(numerator, denominator) if intervals else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "confidence_interval": None if bounds is None else {
            "level": 0.95, "method": "wilson", "lower": bounds[0], "upper": bounds[1],
        },
    }


def _binary(values, name: str) -> list[int]:
    out = []
    for index, value in enumerate(values, 1):
        if not isinstance(value, Integral) or value not in (0, 1):
            raise ValueError(f"{name} 第 {index} 项必须是 0/1 或 bool，实际为 {value!r}")
        out.append(int(value))
    return out


def binary_metrics(truth, predicted, *, intervals: bool = True) -> dict:
    """0=不需要审阅，1=需要审阅。行是真实类别，列是预测类别。

    无分母的指标返回 None；F1 不套用二项区间。intervals=True 假定等权随机样本，
    不适用于 #15 那种按系统是否标记的分层样本；完整集合可传 False。
    """
    truth, predicted = _binary(truth, "truth"), _binary(predicted, "predicted")
    if not truth or len(truth) != len(predicted):
        raise ValueError("truth 与 predicted 必须非空、长度相同")
    if type(intervals) is not bool:
        raise ValueError("intervals 必须是 bool")
    tn = sum(t == 0 and p == 0 for t, p in zip(truth, predicted))
    fp = sum(t == 0 and p == 1 for t, p in zip(truth, predicted))
    fn = sum(t == 1 and p == 0 for t, p in zip(truth, predicted))
    tp = sum(t == 1 and p == 1 for t, p in zip(truth, predicted))
    n = len(truth)
    return {
        "n": n,
        "counts": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "confusion_matrix": {"labels": [0, 1], "rows": "truth", "columns": "predicted",
                             "values": [[tn, fp], [fn, tp]]},
        "positive_support": tp + fn,
        "predicted_positive": tp + fp,
        "recall": _ratio(tp, tp + fn, intervals),
        "precision": _ratio(tp, tp + fp, intervals),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn, False),
        "accuracy": _ratio(tp + tn, n, intervals),
        "push_rate": _ratio(tp + fp, n, intervals),
    }


COLUMNS = ("document_id", "change_id", "needs_review", "predicted_review")


def read_predictions(path: Path) -> list[dict]:
    """严格读取完整评测输入；同一篇文稿不允许重复 change_id。"""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return _prediction_rows(stream)


def _prediction_rows(stream) -> list[dict]:
    rows, seen = [], set()
    reader = csv.DictReader(stream)
    columns = reader.fieldnames or []
    if len(columns) != len(set(columns)) or set(columns) != set(COLUMNS):
        raise ValueError("CSV 必须恰好包含以下四列（不接受分层、权重或其他额外列）：" + ",".join(COLUMNS))
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"第 {reader.line_num} 行列数不正确")
        doc, cid = row["document_id"].strip(), row["change_id"].strip()
        identity = (doc, cid)
        if not doc or not cid or identity in seen:
            raise ValueError(f"第 {reader.line_num} 行文稿/修改 ID 为空或重复")
        if row["needs_review"].strip() not in ("0", "1") or row["predicted_review"].strip() not in ("0", "1"):
            raise ValueError(f"第 {reader.line_num} 行标签和预测必须填 0 或 1，不允许缺失")
        seen.add(identity)
        rows.append({"document_id": doc, "change_id": cid, "needs_review": int(row["needs_review"].strip()),
                     "predicted_review": int(row["predicted_review"].strip())})
    if not rows:
        raise ValueError("CSV 没有评测条目")
    return rows


def read_manifest(path: Path) -> tuple[str, set[tuple[str, str]], str]:
    """独立于预测表预先登记的评测 ID 集与抽样设计；不从预测表推断完整性。"""
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or set(data) != {"manifest_version", "sampling", "items"}:
        raise ValueError("清单必须恰好包含 manifest_version、sampling 和 items")
    if data["manifest_version"] != "1":
        raise ValueError("清单 manifest_version 必须为字符串 1")
    sampling = data["sampling"]
    if sampling not in ("random", "census"):
        raise ValueError("清单只支持 random 或 census；分层样本或其他设计需要专门加权评测")
    items = data["items"]
    if not isinstance(items, list) or not items:
        raise ValueError("清单 items 必须是非空列表")
    expected = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != {"document_id", "change_id"}:
            raise ValueError(f"清单第 {index} 项必须恰好包含 document_id 和 change_id")
        doc, cid = item["document_id"], item["change_id"]
        if not isinstance(doc, str) or not isinstance(cid, str) or not doc.strip() or not cid.strip():
            raise ValueError(f"清单第 {index} 项 ID 必须是非空文本")
        identity = (doc.strip(), cid.strip())
        if identity in expected:
            raise ValueError(f"清单第 {index} 项文稿/修改 ID 重复")
        expected.add(identity)
    return sampling, expected, hashlib.sha256(raw).hexdigest()


def evaluation_scope(path: Path, *, sampling: str, manifest: Path) -> tuple[set, str]:
    """核对评测设计，并固定本次使用的独立清单。"""
    if sampling not in ("random", "census"):
        raise ValueError("sampling 只支持 random 或 census；分层样本需要按设计加权")
    check_distinct_inputs(path, manifest)
    design, expected, manifest_sha = read_manifest(manifest)
    if sampling != design:
        raise ValueError(f"sampling={sampling} 与独立清单登记的 {design} 不一致")
    return expected, manifest_sha


def check_distinct_inputs(path: Path, manifest: Path) -> None:
    if path.resolve() == manifest.resolve():
        raise ValueError("评测清单与预测表不能是同一文件")


def prediction_snapshot(path: Path) -> tuple[list[dict], str]:
    """同一份原始字节用于解析和留档摘要，保留 BOM 和换行对摘要的影响。"""
    raw = path.read_bytes()
    with io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig", newline="") as stream:
        return _prediction_rows(stream), hashlib.sha256(raw).hexdigest()


def evaluate_csv(path: Path, *, sampling: str, manifest: Path) -> dict:
    """核对独立清单后计算。random 清单列出预抽样 ID；census 清单列出完整集合 ID。"""
    expected, manifest_sha = evaluation_scope(path, sampling=sampling, manifest=manifest)
    return evaluate_rows(read_predictions(path), sampling=sampling, expected=expected, manifest_sha=manifest_sha)


def evaluate_rows(rows: list[dict], *, sampling: str, expected: set, manifest_sha: str) -> dict:
    actual = {(r["document_id"], r["change_id"]) for r in rows}
    missing, extra = expected - actual, actual - expected
    if missing or extra:
        raise ValueError(f"预测表与独立清单的 ID 集不一致：缺少 {len(missing)} 条，多出 {len(extra)} 条；未计算指标")
    groups = defaultdict(list)
    for row in rows:
        groups[row["document_id"]].append(row)

    def metrics(selected):
        return binary_metrics([r["needs_review"] for r in selected], [r["predicted_review"] for r in selected],
                              intervals=sampling == "random")

    return {
        "metric_version": "0.2", "sampling": sampling,
        "coverage": {"expected": len(expected), "observed": len(actual), "id_set_matches": True,
                     "manifest_sha256": manifest_sha},
        "interval_note": "95% Wilson 二项区间，假定等权随机样本，不包含标注误差或跨作者泛化不确定性"
                         if sampling == "random" else "完整评测集合的精确计数，不报告抽样置信区间",
        "overall": metrics(rows),
        "per_document": {doc: metrics(selected) for doc, selected in sorted(groups.items())},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="#8 通用二分类评测，不训练、不调阈值")
    parser.add_argument("predictions", type=Path, help="含真实二分类标签和系统预测的 CSV")
    parser.add_argument("--manifest", type=Path, required=True, help="预测生成前独立登记的评测 ID 集和抽样设计 JSON")
    parser.add_argument("--sampling", required=True, choices=("random", "census"), help="随机样本或完整集合；不接受分层样本")
    parser.add_argument("--out", type=Path, help="可选的新 JSON 文件；默认写到 stdout")
    args = parser.parse_args(argv)
    try:
        result = evaluate_csv(args.predictions, sampling=args.sampling, manifest=args.manifest)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.out:
            # x 模式也防止把输入表或已有结果覆盖掉。
            with args.out.open("x", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text, end="")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
