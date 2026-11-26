"""#8：在同一份独立清单、参考答案上比较冻结后的各方案。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .evaluate import check_distinct_inputs, evaluate_rows, evaluation_scope, prediction_snapshot
else:
    from evaluate import check_distinct_inputs, evaluate_rows, evaluation_scope, prediction_snapshot


def compare_schemes(schemes: dict[str, Path], *, baseline: str, sampling: str, manifest: Path) -> dict:
    if len(schemes) < 2 or baseline not in schemes:
        raise ValueError("至少提供两个方案，baseline 必须是其中一个方案名")
    if any(not isinstance(name, str) or not name.strip() or name != name.strip() for name in schemes):
        raise ValueError("方案名必须是无首尾空白的非空文本")
    results, indexed = {}, {}
    expected, manifest_sha = evaluation_scope(next(iter(schemes.values())), sampling=sampling, manifest=manifest)
    for name, path in schemes.items():
        check_distinct_inputs(path, manifest)
        rows, digest = prediction_snapshot(path)
        results[name] = evaluate_rows(rows, sampling=sampling, expected=expected, manifest_sha=manifest_sha)
        results[name]["predictions_sha256"] = digest
        indexed[name] = {(r["document_id"], r["change_id"]): r for r in rows}
    reference = indexed[baseline]
    # 每个方案先经过独立清单的完整性校验，再逐 ID 核对标签，不能用行序配对。
    for name, rows in indexed.items():
        if any(row["needs_review"] != reference[key]["needs_review"] for key, row in rows.items()):
            raise ValueError(f"方案 {name} 与 baseline 的参考标签不一致；未计算消融差值")

    def transitions(rows, keys):
        gained_risky = lost_risky = added_safe = removed_safe = 0
        for key in keys:
            before, after = reference[key]["predicted_review"], rows[key]["predicted_review"]
            truth = reference[key]["needs_review"]
            gained_risky += truth == 1 and before == 0 and after == 1
            lost_risky += truth == 1 and before == 1 and after == 0
            added_safe += truth == 0 and before == 0 and after == 1
            removed_safe += truth == 0 and before == 1 and after == 0
        return {"risky_recovered": gained_risky, "risky_lost": lost_risky,
                "safe_newly_pushed": added_safe, "safe_no_longer_pushed": removed_safe,
                "net_true_positive_change": gained_risky - lost_risky,
                "net_push_change": gained_risky + added_safe - lost_risky - removed_safe}

    for name, rows in indexed.items():
        results[name]["vs_baseline"] = {
            "overall": transitions(rows, reference),
            "per_document": {doc: transitions(rows, [key for key in reference if key[0] == doc])
                             for doc in sorted(results[name]["per_document"])},
        }
    return {"comparison_version": "1", "baseline": baseline, "schemes": results,
            "note": "只比较输入预测，不训练或选择阈值；差值是同一评测集合的计数，不是因果效果或泛化结论。"
                    "是否在预测前冻结参数、材料是否独立，仍需实验记录证明。"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="#8 多方案指标与逐修改配对消融计数")
    parser.add_argument("--scheme", action="append", required=True, help="方案名=四列预测CSV；至少两次")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sampling", choices=("random", "census"), required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        schemes = {}
        for item in args.scheme:
            name, sep, path = item.partition("=")
            if not sep or not path or name in schemes:
                raise ValueError("--scheme 格式须为唯一方案名=路径")
            schemes[name] = Path(path)
        result = compare_schemes(schemes, baseline=args.baseline, sampling=args.sampling, manifest=args.manifest)
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8") as f:
                f.write(content)
        else:
            print(content, end="")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
