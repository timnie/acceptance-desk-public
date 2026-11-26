"""实验训练链路，用可分的编写数值验证，不代表真实论文效果。"""

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from acceptance_desk import audit
from experiments import train


def dataset(path, source, *, protocol="demo", feature_set="rules"):
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    provenance = {"document_id": source + "_r01", "source_document_id": source,
                  "change_ids": [f"c{i}" for i in range(8)],
                  **{name: sha(source + name) for name in train.SOURCE_HASHES}}
    values = {name: 0 for name in train.FEATURES["all"]}
    values.update(length_original=10, length_revised=8, length_ratio=0.8, char_edit_ratio=0.2,
                  paired=1, **{"align_1-1": 1})
    data = {"dataset_version": train.DATASET_VERSION, "target": "needs_review",
            "target_definition": train.TARGET_DEFINITION, "protocol_id": protocol,
            "sources": [provenance],
            "rows": [{"document_id": source + "_r01", "source_document_id": source,
                      **{k: provenance[k] for k in train.SOURCE_FIELDS},
                      "change_id": f"c{i}", "label": "stronger_claim" if i >= 4 else "no_risk",
                      "needs_review": int(i >= 4),
                      "mode": "rules_only", "lexicon_version": "0.1.1", "models": None,
                      "features": {**values, "hedge_removed": i}} for i in range(8)]}
    path.write_text(json.dumps(data))
    return data


def fit_args(tmp_path):
    return ["fit", "--train", str(tmp_path / "train.json"), "--validation", str(tmp_path / "val.json"),
            "--feature-set", "rules", "--min-recall", "0.85", "--out-dir", str(tmp_path / "model")]


def test_fit_freezes_threshold_and_predicts_without_retraining(tmp_path):
    dataset(tmp_path / "train.json", "p02")
    dataset(tmp_path / "val.json", "p04")
    dataset(tmp_path / "test.json", "p05")
    train.main(fit_args(tmp_path))
    meta = json.loads((tmp_path / "model/metadata.json").read_text())
    assert meta["validation"]["recall"]["value"] == 1.0
    assert meta["validation"]["push_rate"]["value"] == 0.5
    assert meta["validation"]["recall"]["confidence_interval"] is None
    before = (tmp_path / "model/classifier.joblib").read_bytes()
    train.main(["predict", str(tmp_path / "test.json"), "--model-dir", str(tmp_path / "model"),
                "--out", str(tmp_path / "predictions.csv")])
    with (tmp_path / "predictions.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert [int(r["predicted_review"]) for r in rows] == [0] * 4 + [1] * 4
    assert before == (tmp_path / "model/classifier.joblib").read_bytes()
    with pytest.raises(SystemExit):
        train.main(fit_args(tmp_path))
    assert before == (tmp_path / "model/classifier.joblib").read_bytes()


@pytest.mark.parametrize("case", ["source_overlap", "protocol", "single_class", "missing", "nan", "label", "version"])
def test_invalid_training_refused_before_artifact_written(tmp_path, case):
    dataset(tmp_path / "train.json", "p02")
    val = dataset(tmp_path / "val.json", "p02" if case == "source_overlap" else "p04")
    if case == "protocol":
        val["protocol_id"] = "different"
    elif case == "single_class":
        val["rows"] = [r for r in val["rows"] if r["needs_review"] == 0]
    elif case == "missing":
        val["rows"][0]["features"].pop(train.RULES[0])
    elif case == "nan":
        val["rows"][0]["features"][train.RULES[0]] = float("nan")
    elif case == "label":
        val["rows"][0]["label"] = "not_present"
    elif case == "version":
        val["rows"][0]["lexicon_version"] = "other"
    (tmp_path / "val.json").write_text(json.dumps(val))
    with pytest.raises(SystemExit) as exc:
        train.main(fit_args(tmp_path))
    assert exc.value.code == 2 and not (tmp_path / "model").exists()


def test_model_features_reject_rules_only_instead_of_imputing_model_output(tmp_path):
    dataset(tmp_path / "train.json", "p02")
    with pytest.raises(ValueError, match="full"):
        train.read_dataset(tmp_path / "train.json", "models")


def test_prepare_requires_exact_labels_and_protects_inputs(tmp_path):
    report = audit("结果可能成立。", "结果成立。", mode="rules_only")
    rp, lp, out = (tmp_path / n for n in ("report.json", "labels.csv", "dataset.json"))
    rp.write_text(json.dumps(report))
    lp.write_text("change_id,label\nc001,stronger_claim\n")
    (tmp_path / "original.txt").write_text("结果可能成立。", encoding="utf-8-sig")
    (tmp_path / "revised.txt").write_text("结果成立。", encoding="utf-8-sig")
    args = ["prepare", str(rp), str(lp), "--document-id", "demo_r1", "--source-document-id", "demo",
            "--protocol-id", "fixture", "--original", str(tmp_path / "original.txt"),
            "--revised", str(tmp_path / "revised.txt"), "--out", str(out)]
    train.main(args)
    data, x, y = train.read_dataset(out, "rules")
    assert y.tolist() == [1] and x[0][0] == 1
    assert "可能" not in out.read_text()
    before = out.read_bytes()
    with pytest.raises(SystemExit):
        train.main(args)
    assert out.read_bytes() == before
    lp.write_text("change_id,label\nu001,stronger_claim\n")
    with pytest.raises(SystemExit):
        train.main(args[:-1] + [str(tmp_path / "another.json")])


def test_threshold_ties_are_not_split_to_fake_recall():
    assert train.choose_threshold([1, 0, 1], [0.5, 0.5, 0.2], 1.0) == 0.2
    with pytest.raises(ValueError):
        train.choose_threshold([1], [float("nan")], 0.85)


def test_predict_rejects_seen_paper_and_modified_threshold(tmp_path):
    dataset(tmp_path / "train.json", "p02")
    dataset(tmp_path / "val.json", "p04")
    dataset(tmp_path / "test.json", "p05")
    train.main(fit_args(tmp_path))
    args = ["predict", str(tmp_path / "train.json"), "--model-dir", str(tmp_path / "model"),
            "--out", str(tmp_path / "pred.csv")]
    with pytest.raises(SystemExit):
        train.main(args)
    meta_path = tmp_path / "model/metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["threshold"] = 0
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(SystemExit):
        train.main(["predict", str(tmp_path / "test.json"), *args[2:]])
    assert not (tmp_path / "pred.csv").exists()


def alias_original(data, sha):
    data["sources"][0]["original_sha256"] = sha
    for row in data["rows"]:
        row["original_sha256"] = sha


def test_same_original_alias_cannot_cross_fit_or_predict(tmp_path):
    training = dataset(tmp_path / "train.json", "p02")
    validation = dataset(tmp_path / "val.json", "renamed_p02")
    alias_original(validation, training["sources"][0]["original_sha256"])
    (tmp_path / "val.json").write_text(json.dumps(validation))
    with pytest.raises(SystemExit) as exc:
        train.main(fit_args(tmp_path))
    assert exc.value.code == 2 and not (tmp_path / "model").exists()
    dataset(tmp_path / "val.json", "p04")
    train.main(fit_args(tmp_path))
    testing = dataset(tmp_path / "test.json", "another_p02_name")
    alias_original(testing, training["sources"][0]["original_sha256"])
    (tmp_path / "test.json").write_text(json.dumps(testing))
    with pytest.raises(SystemExit) as exc:
        train.main(["predict", str(tmp_path / "test.json"), "--model-dir", str(tmp_path / "model"),
                    "--out", str(tmp_path / "prediction.csv")])
    assert exc.value.code == 2 and not (tmp_path / "prediction.csv").exists()


@pytest.mark.parametrize("case", ["old", "list", "null_row", "features", "protocol", "indicator", "negative",
                                  "fraction", "huge", "onehot", "ratio", "source", "hash", "missing_model", "missing_row"])
def test_dataset_structure_and_domains_fail_cleanly(tmp_path, case, capsys):
    data = dataset(tmp_path / "train.json", "p02")
    row = data["rows"][0]
    if case == "old": data["dataset_version"] = "1"
    elif case == "list": data = []
    elif case == "null_row": data["rows"][0] = None
    elif case == "features": row["features"] = None
    elif case == "protocol": data["protocol_id"] = "  "
    elif case == "indicator": row["features"]["model_present"] = 9
    elif case == "negative": row["features"]["hedge_added"] = -1
    elif case == "fraction": row["features"]["numbers_added"] = 0.5
    elif case == "huge": row["features"]["length_ratio"] = 10 ** 1000
    elif case == "onehot": row["features"]["align_1-2"] = 1
    elif case == "ratio": row["features"]["length_ratio"] = -1
    elif case == "source": row["source_document_id"] = "another"
    elif case == "hash": data["sources"][0]["original_sha256"] = "broken"
    elif case == "missing_row": data["rows"].pop()
    else: row.pop("models")
    (tmp_path / "train.json").write_text(json.dumps(data))
    dataset(tmp_path / "val.json", "p04")
    with pytest.raises(SystemExit) as exc:
        train.main(fit_args(tmp_path))
    assert exc.value.code == 2 and not (tmp_path / "model").exists()
    assert "[错误]" in capsys.readouterr().err


@pytest.mark.parametrize("case", ["hash", "nli", "nan", "null_ratio", "wrong_ratio", "missing_change", "string_list"])
def test_prepare_rejects_corrupt_report_without_partial_output(tmp_path, case):
    original, revised = "甲可能成立。乙可能有效。", "甲成立。乙有效。"
    report = audit(original, revised, mode="rules_only")
    if case == "hash": report["document"]["original_sha256"] = "0" * 64
    elif case == "nli":
        report["run"].update(mode="full", models={"nli": {"name": "fixture", "revision": "1"},
                                                "embedding": {"name": "fixture", "revision": "1"}})
    elif case == "nan": report["changes"][0]["features"]["rules"]["length_original"] = float("nan")
    elif case == "null_ratio": report["changes"][0]["features"]["rules"]["char_edit_ratio"] = None
    elif case == "wrong_ratio": report["changes"][0]["features"]["rules"]["char_edit_ratio"] = 0.5
    elif case == "string_list": report["changes"][0]["features"]["rules"]["hedge_added"] = "x"
    else:
        from acceptance_desk.report import _summary
        report["changes"].pop(); report["summary"] = _summary(report["changes"])
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "labels.csv").write_text("change_id,label\n" + "".join(c["id"] + ",semantic_change\n" for c in report["changes"]))
    (tmp_path / "original.txt").write_text(original)
    (tmp_path / "revised.txt").write_text(revised)
    with pytest.raises(SystemExit) as exc:
        train.main(["prepare", str(tmp_path / "report.json"), str(tmp_path / "labels.csv"),
                    "--original", str(tmp_path / "original.txt"), "--revised", str(tmp_path / "revised.txt"),
                    "--document-id", "fixture", "--source-document-id", "fixture", "--protocol-id", "fixture",
                    "--out", str(tmp_path / "out.json")])
    assert exc.value.code == 2 and not (tmp_path / "out.json").exists()


def test_merge_keeps_leaf_sources_and_input_fingerprints(tmp_path):
    a, b, c = [tmp_path / f"{name}.json" for name in ("a", "b", "c")]
    da, db = dataset(a, "p02"), dataset(b, "p03")
    before = [a.read_bytes(), b.read_bytes()]
    train.main(["merge", str(a), str(b), "--out", str(c)])
    merged, x, _ = train.read_dataset(c, "rules")
    assert len(x) == 16 and merged["sources"] == da["sources"] + db["sources"]
    assert [s["sha256"] for s in merged["merged_from"]] == [train.file_sha(a), train.file_sha(b)]
    assert [a.read_bytes(), b.read_bytes()] == before
    for inputs in ([str(a), str(a)], [str(a), str(c)]):
        with pytest.raises(SystemExit):
            train.main(["merge", *inputs, "--out", str(tmp_path / "bad.json")])
    assert not (tmp_path / "bad.json").exists()


@pytest.mark.parametrize("case", ["probability", "sum", "similarity", "missing_pair", "model_version"])
def test_full_feature_semantics_are_checked(tmp_path, case):
    data = dataset(tmp_path / "data.json", "p02")
    for row in data["rows"]:
        row.update(mode="full", models={"nli": {"name": "fixture", "revision": "1"},
                                        "embedding": {"name": "fixture", "revision": "1"}})
        row["features"].update({f"{d}_{k}": v for d in ("forward", "backward")
                                for k, v in (("entailment", 0.8), ("neutral", 0.1), ("contradiction", 0.1))})
        row["features"].update(similarity=0.9, model_present=1)
    train.validate_dataset(data, "all")
    row = data["rows"][0]
    if case == "probability": row["features"]["forward_entailment"] = 9
    elif case == "sum": row["features"]["backward_neutral"] = 0.9
    elif case == "similarity": row["features"]["similarity"] = 1.5
    elif case == "missing_pair": row["features"]["model_present"] = 0
    else: row["models"] = None
    with pytest.raises(ValueError):
        train.validate_dataset(data, "all")



def test_target_is_derived_from_five_class_label(tmp_path):
    """#16：训练目标由五分类推出（label != no_risk），五分类原值保留作解释；不能另填一个对不上的二分类。"""
    report = audit("结果可能成立。\n\n方法不变。", "结果成立。\n\n方法不变了。", mode="rules_only")
    rp, lp, out = (tmp_path / n for n in ("report.json", "labels.csv", "dataset.json"))
    rp.write_text(json.dumps(report))
    ids = [c["id"] for c in report["changes"]]
    lp.write_text("change_id,label\n" + f"{ids[0]},stronger_claim\n{ids[1]},no_risk\n")
    (tmp_path / "original.txt").write_text("结果可能成立。\n\n方法不变。")
    (tmp_path / "revised.txt").write_text("结果成立。\n\n方法不变了。")
    train.main(["prepare", str(rp), str(lp), "--document-id", "d_r1", "--source-document-id", "d",
                "--protocol-id", "fixture", "--original", str(tmp_path / "original.txt"),
                "--revised", str(tmp_path / "revised.txt"), "--out", str(out)])
    data, _, y = train.read_dataset(out, "rules")
    assert y.tolist() == [1, 0] and [r["label"] for r in data["rows"]] == ["stronger_claim", "no_risk"]
    assert data["target_definition"] == train.TARGET_DEFINITION
    data["rows"][1]["needs_review"] = 1                                   # 和标签推不出的目标对不上
    (tmp_path / "bad.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="由 label 推出"):
        train.read_dataset(tmp_path / "bad.json", "rules")


def test_old_needs_review_label_file_is_rejected(tmp_path):
    report = audit("结果可能成立。", "结果成立。", mode="rules_only")
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "labels.csv").write_text("change_id,needs_review\nc001,1\n")
    (tmp_path / "original.txt").write_text("结果可能成立。")
    (tmp_path / "revised.txt").write_text("结果成立。")
    with pytest.raises(SystemExit) as exc:
        train.main(["prepare", str(tmp_path / "report.json"), str(tmp_path / "labels.csv"),
                    "--original", str(tmp_path / "original.txt"), "--revised", str(tmp_path / "revised.txt"),
                    "--document-id", "x_r1", "--source-document-id", "x", "--protocol-id", "fixture",
                    "--out", str(tmp_path / "o.json")])
    assert exc.value.code == 2


def _prepare(tmp_path, report, original, revised, label="stronger_claim"):
    rp, lp, out = (tmp_path / n for n in ("report.json", "labels.csv", "dataset.json"))
    rp.write_text(json.dumps(report))
    lp.write_text("change_id,label\n" + "".join(f"{c['id']},{label}\n" for c in report["changes"]))
    (tmp_path / "original.txt").write_text(original)
    (tmp_path / "revised.txt").write_text(revised)
    train.main(["prepare", str(rp), str(lp), "--document-id", "d_r1", "--source-document-id", "d",
                "--protocol-id", "fixture", "--original", str(tmp_path / "original.txt"),
                "--revised", str(tmp_path / "revised.txt"), "--out", str(out)])
    return json.loads(out.read_text())


def test_attribution_feature_is_counted(tmp_path):
    o, r = "这样形成的纪律是一种防御。", "这样形成的纪律，用某学者的话说，是一种防御。"      # 编写的例句
    data = _prepare(tmp_path, audit(o, r, mode="rules_only"), o, r)
    assert data["rows"][0]["features"]["attribution_added"] == 1


def test_reports_from_lexicons_without_attribution_encode_zero(tmp_path):
    """词表 0.2 以前的报告没有 attribution_added：按 0 编码，并记下报告的词表版本供 fit / merge 拒绝混用。"""
    o, r = "结果可能成立。", "结果成立。"
    report = audit(o, r, mode="rules_only")
    report["run"]["lexicon_version"] = "0.1.2"
    for c in report["changes"]:
        c["features"]["rules"]["delta"].pop("attribution")
        c["features"]["rules"].pop("attribution_added")
    data = _prepare(tmp_path, report, o, r)
    assert data["rows"][0]["features"]["attribution_added"] == 0 and data["rows"][0]["lexicon_version"] == "0.1.2"


def test_current_report_missing_attribution_is_rejected(tmp_path):
    o, r = "结果可能成立。", "结果成立。"
    report = audit(o, r, mode="rules_only")
    report["changes"][0]["features"]["rules"].pop("attribution_added")
    with pytest.raises(SystemExit):
        _prepare(tmp_path, report, o, r)


@pytest.mark.parametrize(("original", "revised", "flagged"), [
    ("结果可能成立。", "结果成立。", True),
    ("调查发现甲与乙相关。", "调查发现甲导致乙。", True),
    ("这条记录有3个例子。", "这条记录有4个例子。", True),
    ("这样形成的纪律是一种防御。", "这样形成的纪律，用某学者的话说，是一种防御。", True),
    ("保留此句。", "保留此句。\n\n新增整句。", True),
    ("保留此句。\n\n删去整句。", "保留此句。", True),
    ("本研究使用问卷调查收集数据。", "本研究采用问卷调查收集数据。", False),
])
def test_rules_hit_matches_rules_only_audit(tmp_path, original, revised, flagged):
    from experiments.evaluate import read_predictions

    report = audit(original, revised, mode="rules_only")
    assert [c["review"]["flagged"] for c in report["changes"]] == [flagged]
    # 故意将答案设成预测的反面，验证推送与人工标签互不依赖。
    _prepare(tmp_path, report, original, revised, label="no_risk" if flagged else "ai_slop")
    out = tmp_path / "baseline.csv"
    args = ["rules-hit", str(tmp_path / "dataset.json"), "--out", str(out)]
    train.main(args)
    assert read_predictions(out) == [{"document_id": "d_r1", "change_id": report["changes"][0]["id"],
                                     "needs_review": int(not flagged), "predicted_review": int(flagged)}]
    before = out.read_bytes()
    with pytest.raises(SystemExit):
        train.main(args)
    assert out.read_bytes() == before


def test_rules_hit_ignores_model_scores_and_unflagged_rule_features(tmp_path):
    from experiments.evaluate import read_predictions

    path = tmp_path / "full.json"
    data = dataset(path, "invented")
    for row in data["rows"]:
        row["mode"] = "full"
        row["models"] = {k: {"name": "fixture", "revision": "1"} for k in ("nli", "embedding")}
        row["features"].update(forward_contradiction=1, backward_contradiction=1, model_present=1,
                               certainty_removed=1, connective_removed=1)
    path.write_text(json.dumps(data))
    out = tmp_path / "baseline.csv"
    train.main(["rules-hit", str(path), "--out", str(out)])
    assert [r["predicted_review"] for r in read_predictions(out)] == [0] + [1] * 7


def test_rules_hit_rejects_mixed_lexicons_before_writing(tmp_path):
    first = dataset(tmp_path / "first.json", "first")
    second = dataset(tmp_path / "second.json", "second")
    for row in second["rows"]:
        row["lexicon_version"] = "0.2"
    first["sources"].extend(second["sources"])
    first["rows"].extend(second["rows"])
    path, out = tmp_path / "mixed.json", tmp_path / "baseline.csv"
    path.write_text(json.dumps(first))
    with pytest.raises(SystemExit) as exc:
        train.main(["rules-hit", str(path), "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()


def test_logistic_c_is_frozen_and_independent_prediction_reloads_it(tmp_path):
    import joblib
    for name, source in (("train", "p02"), ("val", "p04"), ("test", "p05")):
        dataset(tmp_path / f"{name}.json", source)
    train.main(fit_args(tmp_path))
    default = joblib.load(tmp_path / "model/classifier.joblib")
    args = fit_args(tmp_path)
    args[-1] = str(tmp_path / "regularized")
    train.main([*args, "--logistic-c", "0.1"])
    frozen = joblib.load(tmp_path / "regularized/classifier.joblib")
    meta = json.loads((tmp_path / "regularized/metadata.json").read_text())
    assert meta["estimator"] == {"name": "logistic_regression", "C": 0.1,
                                 "class_weight": "balanced", "max_iter": 2000, "random_state": 0}
    assert np.linalg.norm(frozen["model"][-1].coef_) < np.linalg.norm(default["model"][-1].coef_)
    before = (tmp_path / "regularized/classifier.joblib").read_bytes()
    train.main(["predict", str(tmp_path / "test.json"), "--model-dir", str(tmp_path / "regularized"),
                "--out", str(tmp_path / "predictions.csv")])
    _, x, _ = train.read_dataset(tmp_path / "test.json", "rules")
    with (tmp_path / "predictions.csv").open(encoding="utf-8-sig") as f:
        actual = [int(row["predicted_review"]) for row in csv.DictReader(f)]
    assert actual == (frozen["model"].predict_proba(x)[:, 1] >= meta["threshold"]).astype(int).tolist()
    assert before == (tmp_path / "regularized/classifier.joblib").read_bytes()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_logistic_c_writes_no_model(tmp_path, value, capsys):
    dataset(tmp_path / "train.json", "p02")
    dataset(tmp_path / "val.json", "p04")
    with pytest.raises(SystemExit) as exc:
        train.main([*fit_args(tmp_path), "--logistic-c=" + value])
    assert exc.value.code == 2
    assert not (tmp_path / "model").exists()
    assert "logistic-c 必须为有限正数" in capsys.readouterr().err
