"""needs_review 二分类训练实验（#16 已定）：标签文件给五分类，训练目标由 label != no_risk 推出。

五分类原值随数据保留，只用于解释，不进特征。不修改 audit/schema。

prepare 从逐条五分类标签准备数据；rules-hit 导出规则基线；fit 只读训练/验证集；predict 使用冻结阈值。
#16 已定（2026-11-13）；#17 的篇目划分未定。不将单元标签广播成逐记录标签。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import shutil
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from acceptance_desk.text import normalize
from acceptance_desk.review import RULE_LIST_REASONS
from acceptance_desk.rules import levenshtein

from acceptance_desk.feature_vector import (
    ALIGNMENTS, FEATURES, LISTS, MODELS, RULES, absent_allowed, vector,
)

if __package__:
    from .evaluate import binary_metrics, COLUMNS
    from .experiment_io import file_sha, write_new_files
    from .report_checks import check_source_report
else:
    from evaluate import binary_metrics, COLUMNS
    from experiment_io import file_sha, write_new_files
    from report_checks import check_source_report

DATASET_VERSION = "4"   # 3：标签改为五分类、目标由标签推出；4：规则特征加入 attribution_added
ARTIFACT_VERSION = "4"
LABELS = ("semantic_change", "stronger_claim", "weaker_claim", "ai_slop", "no_risk")
TARGET_DEFINITION = "needs_review = int(label != \"no_risk\")"
SOURCE_FIELDS = ("source_document_id", "original_sha256", "revised_sha256", "report_sha256", "labels_sha256")
SOURCE_HASHES = (*SOURCE_FIELDS[1:], "original_file_sha256", "revised_file_sha256")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} 必须为无首尾空白的非空文本")


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} 必须为 SHA256")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _model_info(mode, models):
    if mode == "rules_only":
        if models is not None:
            raise ValueError("rules_only 的模型版本必须为 null")
    elif mode == "full":
        if not isinstance(models, dict) or set(models) != {"nli", "embedding"}:
            raise ValueError("full 模式需登记 nli 和 embedding 模型版本")
        for model in models.values():
            if not isinstance(model, dict):
                raise ValueError("模型版本必须是对象")
            for key in ("name", "revision"):
                _text(model.get(key), "模型 " + key)
    else:
        raise ValueError("数据模式必须是 rules_only 或 full")


def _check_vector(row):
    values = row.get("features")
    if not isinstance(values, dict) or set(values) != set(FEATURES["all"]):
        raise ValueError("数据需包含完整、已知的数值特征集合")
    if any(not _number(v) for v in values.values()):
        raise ValueError("所有特征必须是有限数值，不能是 bool")
    for name in (*LISTS, "length_original", "length_revised"):
        if values[name] < 0 or values[name] != int(values[name]):
            raise ValueError(f"{name} 必须是非负整数计数")
    indicators = ("causal_upgrade", "paired", "model_present", *[f"align_{a}" for a in ALIGNMENTS])
    if any(values[k] not in (0, 1) for k in indicators):
        raise ValueError("指示特征必须为 0/1")
    active = [a for a in ALIGNMENTS if values[f"align_{a}"] == 1]
    if len(active) != 1:
        raise ValueError("对齐特征必须恰有一项为 1")
    paired = active[0] not in ("0-1", "1-0")
    if values["paired"] != int(paired):
        raise ValueError("paired 与对齐类型不一致")
    lo, lr = values["length_original"], values["length_revised"]
    if (paired and (lo <= 0 or lr <= 0)) or (active[0] == "0-1" and (lo != 0 or lr <= 0)) or (active[0] == "1-0" and (lo <= 0 or lr != 0)):
        raise ValueError("两侧长度与对齐类型不一致")
    if values["length_ratio"] != (round(lr / lo, 3) if paired else 0) or values["char_edit_ratio"] < 0:
        raise ValueError("长度比例或编辑比例不合法")
    if paired and values["char_edit_ratio"] > round(max(lo, lr) / lo, 3):
        raise ValueError("编辑比例超出两侧长度允许的范围")
    if not paired and (values["char_edit_ratio"] or values["causal_upgrade"]):
        raise ValueError("单侧记录的编辑比例和因果升级必须为零")
    present = row["mode"] == "full" and paired
    if values["model_present"] != int(present):
        raise ValueError("full 双侧记录必须有模型特征；单侧和 rules_only 不得伪造模型特征")
    if not present:
        if any(values[k] != 0 for k in MODELS):
            raise ValueError("缺失模型的向量必须用零和 model_present=0 编码")
    else:
        for direction in ("forward", "backward"):
            probs = [values[f"{direction}_{k}"] for k in ("entailment", "neutral", "contradiction")]
            if any(not 0 <= v <= 1 for v in probs) or abs(sum(probs) - 1) > 0.001:
                raise ValueError("NLI 概率必须在 [0,1] 内且和为 1")
        if not -1 <= values["similarity"] <= 1:
            raise ValueError("similarity 必须在 [-1,1] 内")


def validate_dataset(data: dict, feature_set: str) -> tuple[dict, np.ndarray, np.ndarray]:
    if not isinstance(data, dict) or data.get("dataset_version") != DATASET_VERSION:
        raise ValueError(f"只接受 dataset_version={DATASET_VERSION}；旧数据须用 prepare 和对应全文重新生成，不能手补指纹")
    if data.get("target") != "needs_review" or data.get("target_definition") != TARGET_DEFINITION:
        raise ValueError(f"target 必须为 needs_review，且定义为 {TARGET_DEFINITION}")
    _text(data.get("protocol_id"), "protocol_id")
    if feature_set not in FEATURES:
        raise ValueError("未知特征方案")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("数据集为空")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("数据缺少来源清单 sources")
    indexed, original_ids, source_ids, revisions = {}, {}, {}, set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("来源清单项必须是对象")
        for name in ("document_id", "source_document_id"):
            _text(source.get(name), name)
        for name in SOURCE_HASHES:
            _hash(source.get(name), name)
        doc, sid, sha = source["document_id"], source["source_document_id"], source["original_sha256"]
        revision = (sha, source["revised_sha256"])
        if doc in indexed or revision in revisions:
            raise ValueError("来源清单有重复文稿或同一轮原改稿的别名")
        if original_ids.setdefault(sha, sid) != sid or source_ids.setdefault(sid, sha) != sha:
            raise ValueError("原稿指纹与来源编号的对应关系不一致")
        indexed[doc] = source
        revisions.add(revision)
    expected = set()
    for doc, source in indexed.items():
        ids = source.get("change_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("来源清单必须登记本篇全部 change_ids")
        for cid in ids:
            _text(cid, "来源 change_id")
        if len(ids) != len(set(ids)):
            raise ValueError("来源清单的 change_ids 重复")
        expected.update((doc, cid) for cid in ids)
    seen, used, document_signatures = set(), set(), {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("数据行必须是对象")
        for name in ("document_id", "source_document_id", "change_id"):
            _text(row.get(name), name)
        source = indexed.get(row["document_id"])
        if source is None or any(row.get(k) != source[k] for k in SOURCE_FIELDS):
            raise ValueError("数据行的身份或指纹与来源清单不符")
        used.add(row["document_id"])
        key = (row["document_id"], row["change_id"])
        if key in seen:
            raise ValueError("数据有重复的文稿/修改编号")
        seen.add(key)
        if row.get("label") not in LABELS:
            raise ValueError("label 必须是五分类代码之一，不能为空或文风选项")
        if row.get("needs_review") != int(row["label"] != "no_risk"):
            raise ValueError("needs_review 必须由 label 推出（label != no_risk），不能单独填写")
        if feature_set != "rules" and row.get("mode") != "full":
            raise ValueError("模型特征实验必须使用 full 报告，不能把规则回退当模型特征")
        _text(row.get("lexicon_version"), "lexicon_version")
        if "models" not in row:
            raise ValueError("数据缺少模型版本字段 models")
        _model_info(row.get("mode"), row.get("models"))
        _check_vector(row)
        signature = json.dumps({k: row[k] for k in ("mode", "lexicon_version", "models")}, sort_keys=True)
        if document_signatures.setdefault(row["document_id"], signature) != signature:
            raise ValueError("同一文稿的特征版本不一致")
    if used != set(indexed):
        raise ValueError("来源清单包含没有任何数据行的文稿")
    if seen != expected:
        raise ValueError("数据行未完整对应来源清单中的修改编号，存在漏行或额外行")
    return data, np.array([[r["features"][k] for k in FEATURES[feature_set]] for r in rows]), np.array([r["needs_review"] for r in rows])


def read_dataset(path: Path, feature_set: str) -> tuple[dict, np.ndarray, np.ndarray]:
    return validate_dataset(json.loads(path.read_text(encoding="utf-8-sig")), feature_set)


def choose_threshold(truth, scores, min_recall: float) -> float:
    truth, scores = np.asarray(truth), np.asarray(scores)
    if truth.ndim != 1 or scores.ndim != 1 or len(truth) != len(scores) or not len(truth):
        raise ValueError("验证标签与概率必须非空且等长")
    if not np.isin(truth, [0, 1]).all() or not np.isfinite(scores).all() or not ((scores >= 0) & (scores <= 1)).all():
        raise ValueError("验证标签必须为 0/1，概率必须在 [0,1] 内")
    if not 0 < min_recall <= 1 or not math.isfinite(min_recall):
        raise ValueError("min_recall 必须在 (0,1] 内")
    if not any(truth):
        raise ValueError("验证集无正类，无法选择召回率阈值")
    candidates = sorted(set(float(v) for v in scores), reverse=True)
    eligible = [t for t in candidates if sum((scores >= t) & (truth == 1)) / sum(truth) >= min_recall]
    # 概率阈值越高推送越少；相同概率的条目不能拆开取舍。
    return max(eligible)


def signatures(data: dict, feature_set: str) -> set[str]:
    return {json.dumps({"lexicon_version": row["lexicon_version"],
                        **({"models": row["models"]} if feature_set != "rules" else {})}, sort_keys=True)
            for row in data["rows"]}


def cmd_prepare(args):
    if any(not getattr(args, k).strip() or getattr(args, k) != getattr(args, k).strip()
           for k in ("document_id", "source_document_id", "protocol_id")):
        raise ValueError("文稿编号、来源篇目编号与口径编号不能为空或带首尾空白")
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    documents = {side: normalize(getattr(args, side).read_text(encoding="utf-8-sig")) for side in ("original", "revised")}
    check_source_report(report, **documents)
    _model_info(report["run"]["mode"], report["run"]["models"])
    for change in report["changes"]:
        rules, features = change["features"]["rules"], change["features"]
        version = report["run"]["lexicon_version"]
        present = [k for k in LISTS if not (k not in rules and absent_allowed(k, version))]
        if any(k not in rules or not isinstance(rules[k], list) or any(not isinstance(t, str) for t in rules[k])
               for k in present):
            raise ValueError("规则词条特征必须为文本列表（只有词表版本本就没有的类别可以缺）")
        if type(rules["causal_upgrade"]) is not bool:
            raise ValueError("causal_upgrade 必须为 bool")
        for side in ("original", "revised"):
            if type(rules["length_" + side]) is not int or rules["length_" + side] != len(change[side]["text"]):
                raise ValueError("规则长度与原改句不一致")
        if change["alignment"] not in ("0-1", "1-0") and any(
                not _number(rules[k]) for k in ("length_ratio", "char_edit_ratio")):
            raise ValueError("双侧记录必须包含有限的长度比例与编辑比例")
        if change["alignment"] not in ("0-1", "1-0"):
            expected_edit = round(levenshtein(change["original"]["text"], change["revised"]["text"]) /
                                  len(change["original"]["text"]), 3)
            if rules["char_edit_ratio"] != expected_edit:
                raise ValueError("编辑比例与原改句不一致")
        if report["run"]["mode"] == "full" and change["alignment"] not in ("0-1", "1-0"):
            if features["nli"] is None or features["similarity"] is None:
                raise ValueError("full 双侧记录缺少 NLI 或 similarity")
    with args.labels.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["change_id", "label"]:
            raise ValueError("标签 CSV 必须恰有 change_id,label 两列（五分类代码），单元或文风标签不适用")
        labels = {}
        for row in reader:
            cid = row["change_id"]
            if None in row or row["label"] not in LABELS or not cid or cid in labels:
                raise ValueError("标签有空值、非五分类代码或重复编号")
            labels[cid] = row["label"]
    if len({c["id"] for c in report["changes"]}) != len(report["changes"]) or set(labels) != {c["id"] for c in report["changes"]}:
        raise ValueError("标签必须完整覆盖原始报告的修改编号")
    source = {"document_id": args.document_id, "source_document_id": args.source_document_id,
              "change_ids": [c["id"] for c in report["changes"]],
              **{side + "_sha256": report["document"][side + "_sha256"] for side in documents},
              "report_sha256": file_sha(args.report), "labels_sha256": file_sha(args.labels),
              **{side + "_file_sha256": file_sha(getattr(args, side)) for side in documents}}
    data = {"dataset_version": DATASET_VERSION, "target": "needs_review", "target_definition": TARGET_DEFINITION,
            "protocol_id": args.protocol_id, "sources": [source],
            "rows": [{"document_id": args.document_id, "source_document_id": args.source_document_id,
                      **{k: source[k] for k in SOURCE_FIELDS},
                      "change_id": c["id"], "label": labels[c["id"]], "needs_review": int(labels[c["id"]] != "no_risk"),
                      "features": vector(c, report["run"]["lexicon_version"]),
                      "mode": report["run"]["mode"], "lexicon_version": report["run"]["lexicon_version"],
                      "models": report["run"]["models"]} for c in report["changes"]]}
    validate_dataset(data, "rules")
    write_new_files([(args.out, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")])


def cmd_merge(args):
    inputs = [read_dataset(path, "rules")[0] for path in args.datasets]
    if len({data["protocol_id"] for data in inputs}) != 1:
        raise ValueError("合并数据的 protocol_id 不一致")
    data = {"dataset_version": DATASET_VERSION, "target": "needs_review", "target_definition": TARGET_DEFINITION,
            "protocol_id": inputs[0]["protocol_id"],
            "sources": [source for item in inputs for source in item["sources"]],
            "rows": [row for item in inputs for row in item["rows"]],
            "merged_from": [{"file": p.name, "sha256": file_sha(p)} for p in args.datasets]}
    validate_dataset(data, "rules")
    if len(signatures(data, "all")) != 1:
        raise ValueError("合并数据的词表或模型版本不一致")
    write_new_files([(args.out, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")])


def check_isolation(left, right):
    for field in ("source_document_id", "original_sha256"):
        if {s[field] for s in left["sources"]} & {s[field] for s in right["sources"]}:
            raise ValueError("训练、验证和测试必须按原稿篇目及内容指纹隔离，同篇改编号也不能跨集合")


def cmd_fit(args):
    if not math.isfinite(args.logistic_c) or args.logistic_c <= 0:
        raise ValueError("logistic-c 必须为有限正数（值越小，约束越强）")
    train, x, y = read_dataset(args.train, args.feature_set)
    val, vx, vy = read_dataset(args.validation, args.feature_set)
    if train["protocol_id"] != val["protocol_id"]:
        raise ValueError("训练集与验证集的口径 protocol_id 不同")
    check_isolation(train, val)
    if signatures(train, args.feature_set) != signatures(val, args.feature_set) or len(signatures(train, args.feature_set)) != 1:
        raise ValueError("训练/验证特征的词表或模型版本不一致")
    if len(set(y)) != 2:
        raise ValueError("训练集必须包含两个类别")
    estimator = {"name": "logistic_regression", "C": args.logistic_c,
                 "class_weight": "balanced", "max_iter": 2000, "random_state": 0}
    model = make_pipeline(StandardScaler(), LogisticRegression(**{k: v for k, v in estimator.items() if k != "name"}))
    model.fit(x, y)
    scores = model.predict_proba(vx)[:, 1]
    threshold = choose_threshold(vy, scores, args.min_recall)
    metadata = {"artifact_version": ARTIFACT_VERSION, "target": "needs_review", "target_definition": TARGET_DEFINITION,
                "protocol_id": train["protocol_id"],
                "feature_set": args.feature_set, "feature_names": FEATURES[args.feature_set],
                "estimator": estimator,
                "threshold": threshold, "min_recall": args.min_recall, "signatures": sorted(signatures(train, args.feature_set)),
                "train_sha256": file_sha(args.train), "validation_sha256": file_sha(args.validation),
                "source_document_ids": sorted({r["source_document_id"] for d in (train, val) for r in d["rows"]}),
                "original_sha256s": sorted({s["original_sha256"] for d in (train, val) for s in d["sources"]}),
                "validation": binary_metrics(vy, (scores >= threshold).astype(int), intervals=False),
                "note": "验证集用于选阈值；不是最终测试结果，不改变产品标签或自动通过行为"}
    args.out_dir.mkdir()  # 必须是新目录，拒绝覆盖旧模型或实验
    try:
        joblib.dump({"model": model, "metadata": metadata}, args.out_dir / "classifier.joblib")
        metadata["model_sha256"] = file_sha(args.out_dir / "classifier.joblib")
        (args.out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    except Exception:
        shutil.rmtree(args.out_dir)
        raise


def frozen_metadata(model_dir: Path) -> dict:
    meta = json.loads((model_dir / "metadata.json").read_text())
    if not isinstance(meta, dict) or meta.get("artifact_version") != ARTIFACT_VERSION:
        raise ValueError(f"只接受 artifact_version={ARTIFACT_VERSION}；旧模型须由新版数据重新训练并冻结，不能手补指纹")
    if meta["feature_names"] != FEATURES.get(meta["feature_set"]) or meta["model_sha256"] != file_sha(model_dir / "classifier.joblib"):
        raise ValueError("模型文件与冻结的元数据不一致")
    return meta


def frozen_estimator(model_dir: Path, meta: dict):
    artifact = joblib.load(model_dir / "classifier.joblib")  # 仅使用本项目生成且来源可信的本地模型
    if json.dumps(artifact["metadata"], sort_keys=True, allow_nan=False) != json.dumps(
            {k: v for k, v in meta.items() if k != "model_sha256"}, sort_keys=True, allow_nan=False):
        raise ValueError("冻结阈值或元数据被修改，与模型内保存的记录不一致")
    return artifact["model"]


def cmd_predict(args):
    meta = frozen_metadata(args.model_dir)
    data, x, _ = read_dataset(args.dataset, meta["feature_set"])
    if data["protocol_id"] != meta["protocol_id"] or sorted(signatures(data, meta["feature_set"])) != meta["signatures"]:
        raise ValueError("预测数据的口径或特征版本与模型不一致")
    if {r["source_document_id"] for r in data["rows"]} & set(meta["source_document_ids"]):
        raise ValueError("评测预测不能使用训练或选阈值时已见过的原始篇目")
    if {s["original_sha256"] for s in data["sources"]} & set(meta["original_sha256s"]):
        raise ValueError("评测预测原稿指纹与训练或验证重叠，不能通过换编号绕过隔离")
    scores = frozen_estimator(args.model_dir, meta).predict_proba(x)[:, 1]
    write_predictions(args.out, data["rows"], scores >= meta["threshold"])


def write_predictions(path, rows, predictions):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows({"document_id": row["document_id"], "change_id": row["change_id"],
                      "needs_review": int(row["needs_review"]), "predicted_review": int(prediction)}
                     for row, prediction in zip(rows, predictions, strict=True))
    write_new_files([(path, output.getvalue())])


def rules_hit(features):
    # 与 rules_only 的候选原因一致：单侧对齐、因果升级或任一触发审阅的词条。
    return (not features["paired"] or bool(features["causal_upgrade"])
            or any(features[key] for _, key, _ in RULE_LIST_REASONS))


def cmd_rules_hit(args):
    data, _, _ = read_dataset(args.dataset, "rules")
    if len(signatures(data, "rules")) != 1:
        raise ValueError("规则基线的词表版本必须一致")
    rows = data["rows"]
    write_predictions(args.out, rows, (rules_hit(row["features"]) for row in rows))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("report", type=Path); p.add_argument("labels", type=Path)
    p.add_argument("--original", type=Path, required=True, help="与报告绑定的原稿全文")
    p.add_argument("--revised", type=Path, required=True, help="与报告绑定的改稿全文")
    for name in ("document-id", "source-document-id", "protocol-id"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--out", type=Path, required=True); p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("merge", help="核对多篇数据并保留来源清单，只写新文件")
    p.add_argument("datasets", nargs="+", type=Path)
    p.add_argument("--out", type=Path, required=True); p.set_defaults(func=cmd_merge)
    p = sub.add_parser("rules-hit", help="导出规则命中即推送的四列预测表")
    p.add_argument("dataset", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_rules_hit)
    p = sub.add_parser("fit")
    p.add_argument("--train", type=Path, required=True); p.add_argument("--validation", type=Path, required=True)
    p.add_argument("--feature-set", choices=FEATURES, required=True)
    p.add_argument("--logistic-c", type=float, default=1.0, help="有限正数；越小约束越强，默认保持 C=1")
    p.add_argument("--min-recall", type=float, required=True)
    p.add_argument("--out-dir", type=Path, required=True); p.set_defaults(func=cmd_fit)
    p = sub.add_parser("predict")
    p.add_argument("dataset", type=Path); p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True); p.set_defaults(func=cmd_predict)
    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, KeyError, TypeError, OSError, AttributeError, IndexError, OverflowError, EOFError) as exc:
        ap.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
