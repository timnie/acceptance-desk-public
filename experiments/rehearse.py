"""虚构材料的完整实验链路演练。fixture 使用编写的模型特征；local 使用本地真实权重。

不读取真实材料，不代表内容口径或划分方案已采纳，所有演练指标都不是论文效果。
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import csv
import io
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from acceptance_desk import audit

if __package__:
    from . import train
    from .compare import compare_schemes
    from .evaluate import evaluate_csv
    from .experiment_io import write_new_files
else:
    import train
    from compare import compare_schemes
    from evaluate import evaluate_csv
    from experiment_io import write_new_files


class _FixtureFeatures:
    """仅供演练的已知答案特征，名字中明确标为 fixture，不冒充 NLI/BGE 输出。"""
    info = {"nli": {"name": "fixture-not-a-real-nli-model", "revision": "1"},
            "embedding": {"name": "fixture-not-a-real-embedding-model", "revision": "1"}}

    def nli(self, original, revised):
        changed = "可能" in original and "可能" not in revised
        return {"entailment": 0.1 if changed else 0.8,
                "neutral": 0.8 if changed else 0.1, "contradiction": 0.1}

    def similarity(self, original, revised):
        return 0.9


PAIRS = [  # 编写的例句与五分类标签；训练目标由 label != no_risk 推出
    ("本研究在三所学校开展问卷调查。", "本研究在五所学校开展问卷调查。", "semantic_change"),
    ("这一结果可能说明方案有效。", "这一结果说明方案有效。", "stronger_claim"),
    ("该结论可能成立。", "该结论必然成立。", "stronger_claim"),
    ("本研究使用问卷调查收集数据。", "本研究采用问卷调查收集数据。", "no_risk"),
    ("本节共分为三论。", "本节共分为三轮。", "no_risk"),
    ("这些材料将用于后续分析。", "这些材料会用于后续分析。", "no_risk"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def run_rehearsal(out_dir: Path, *, model_features="fixture", model_cache=None) -> dict:
    if model_features not in ("fixture", "local"):
        raise ValueError("model_features 必须为 fixture 或 local")
    out_dir.mkdir()  # 只使用新目录；失败时不清理已有目录。
    try:
        context = (patch("acceptance_desk.report.FeatureModels.load", return_value=_FixtureFeatures())
                   if model_features == "fixture" else nullcontext())
        with context:
            for name in ("train_a", "train_b", "validation", "test"):
                originals, revisions = [f"这是完全虚构的演练材料{name}。"], [f"这是完全虚构的演练材料{name}。"]
                for i, (original, revised, _) in enumerate(PAIRS):
                    anchor = f"第{i}个演练间隔保持不变。"
                    originals.extend((original, anchor)); revisions.extend((revised, anchor))
                original, revised = "\n\n".join(originals), "\n\n".join(revisions)
                report = audit(original, revised, mode="full", model_cache=model_cache)
                if not all(c["decision"]["status"] == "pending" for c in report["changes"]):
                    raise ValueError("演练不能自动决定修改")
                labels = {(o, r): value for o, r, value in PAIRS}
                buf = io.StringIO()
                writer = csv.writer(buf); writer.writerow(("change_id", "label"))
                for change in report["changes"]:
                    pair = (change["original"]["text"], change["revised"]["text"])
                    if pair not in labels:
                        raise ValueError("演练对齐与编写的逐条标签不符，拒绝猜测或广播标签")
                    writer.writerow((change["id"], labels[pair]))
                op, rp, report_path, lp = [out_dir / f"{name}_{suffix}" for suffix in
                                           ("original.txt", "revised.txt", "report.json", "labels.csv")]
                write_new_files([(op, original), (rp, revised), (report_path, _json(report)), (lp, buf.getvalue())])
                train.main(["prepare", str(report_path), str(lp), "--original", str(op), "--revised", str(rp),
                            "--document-id", name, "--source-document-id", name, "--protocol-id", "invented_rehearsal_v1",
                            "--out", str(out_dir / f"{name}.json")])
        train.main(["merge", str(out_dir / "train_a.json"), str(out_dir / "train_b.json"),
                    "--out", str(out_dir / "training.json")])
        testing, _, _ = train.read_dataset(out_dir / "test.json", "rules")
        manifest = out_dir / "evaluation_manifest.json"
        # 在生成预测前登记完整评测集合，不从预测表倒推清单。
        write_new_files([(manifest, _json({"manifest_version": "1", "sampling": "census",
                                         "items": [{"document_id": r["document_id"], "change_id": r["change_id"]}
                                                   for r in testing["rows"]]}))])
        baseline = out_dir / "prediction_rules_hit.csv"
        train.main(["rules-hit", str(out_dir / "test.json"), "--out", str(baseline)])
        schemes, frozen = {"rules_hit": baseline}, {}
        for feature_set in train.FEATURES:
            model_dir, prediction = out_dir / f"model_{feature_set}", out_dir / f"prediction_{feature_set}.csv"
            train.main(["fit", "--train", str(out_dir / "training.json"), "--validation", str(out_dir / "validation.json"),
                        "--feature-set", feature_set, "--min-recall", "0.85", "--out-dir", str(model_dir)])
            before = {p.name: train.file_sha(p) for p in model_dir.iterdir()}
            train.main(["predict", str(out_dir / "test.json"), "--model-dir", str(model_dir), "--out", str(prediction)])
            if before != {p.name: train.file_sha(p) for p in model_dir.iterdir()}:
                raise ValueError("预测过程修改了冻结模型或元数据")
            frozen[feature_set] = before
            result = evaluate_csv(prediction, sampling="census", manifest=manifest)
            write_new_files([(out_dir / f"metrics_{feature_set}.json", _json(result))])
            schemes[feature_set] = prediction
        comparison = compare_schemes(schemes, baseline="rules_hit", sampling="census", manifest=manifest)
        write_new_files([(out_dir / "comparison.json", _json(comparison))])
        run = {"rehearsal_version": "1", "material_origin": "invented", "model_features": model_features,
               "not_effect_evidence": True, "protocol_id": "invented_rehearsal_v1", "min_recall": 0.85,
               "frozen_artifacts_unchanged": frozen,
               "files": {p.relative_to(out_dir).as_posix(): train.file_sha(p)
                         for p in sorted(out_dir.rglob("*")) if p.is_file()},
               "note": "仅验证工程链路；fixture 特征是编写的替身，不是模型输出；local 仍用虚构材料，不是实际论文效果。"}
        write_new_files([(out_dir / "rehearsal.json", _json(run))])
        return run
    except BaseException:
        shutil.rmtree(out_dir)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True, help="必须是新目录")
    parser.add_argument("--model-features", choices=("fixture", "local"), default="fixture")
    parser.add_argument("--model-cache", help="local 模式所用的已有权重目录；不下载")
    args = parser.parse_args(argv)
    try:
        run_rehearsal(args.out_dir, model_features=args.model_features, model_cache=args.model_cache)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"[错误] {exc}\n")
    print("虚构材料演练通过：prepare → merge → rules_hit 与 rules/models/all → 冻结预测 → evaluate → compare；不是效果评测")


if __name__ == "__main__":
    main()
