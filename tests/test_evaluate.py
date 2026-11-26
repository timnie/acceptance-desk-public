"""#8 评测的已知答案、缺失分母、CSV 输入与命令行回归。"""

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluate", ROOT / "experiments/evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_known_confusion_and_metrics():
    # TN=2, FP=1, FN=2, TP=3，专门让精确率/召回率分母和矩阵不对称。
    r = evaluate.binary_metrics([0, 0, 0, 1, 1, 1, 1, 1], [0, 0, 1, 0, 0, 1, 1, 1])
    assert r["counts"] == {"tn": 2, "fp": 1, "fn": 2, "tp": 3}
    assert r["confusion_matrix"]["values"] == [[2, 1], [2, 3]]
    assert r["recall"]["numerator"] == 3 and r["recall"]["denominator"] == 5
    assert r["recall"]["value"] == pytest.approx(3 / 5)
    assert r["precision"]["value"] == pytest.approx(3 / 4)
    assert r["f1"]["value"] == pytest.approx(6 / 9)
    assert r["accuracy"]["value"] == pytest.approx(5 / 8)
    assert r["push_rate"]["numerator"] == 4 and r["push_rate"]["denominator"] == 8
    assert r["f1"]["confidence_interval"] is None


def test_wilson_17_of_20_known_values():
    lo, hi = evaluate.wilson_interval(17, 20)
    assert lo == pytest.approx(0.6395811353)
    assert hi == pytest.approx(0.9476312541)
    assert evaluate.wilson_interval(0, 0) is None


@pytest.mark.parametrize("successes,total", [(0, 10), (10, 10), (1, 1)])
def test_wilson_extremes_are_bounded(successes, total):
    lo, hi = evaluate.wilson_interval(successes, total)
    assert 0 <= lo <= successes / total <= hi <= 1
    assert hi > lo


@pytest.mark.parametrize("total", [1, 3, 7, 10, 30, 97, 1000])
def test_wilson_endpoints_are_exact_at_zero_and_all(total):
    # Linux CI 上 0/10 的下界曾算成 2.8e-17；端点不应依赖平台浮点舍入。
    assert evaluate.wilson_interval(0, total)[0] == 0.0
    assert evaluate.wilson_interval(total, total)[1] == 1.0


@pytest.mark.parametrize("truth,predicted,precision,recall,f1", [
    ([0, 0], [0, 0], None, None, None),
    ([1, 1], [0, 0], None, 0, 0),
    ([0, 0], [1, 1], 0, None, 0),
    ([1, 1], [1, 1], 1, 1, 1),
])
def test_no_positive_denominators_are_not_silently_zero(truth, predicted, precision, recall, f1):
    r = evaluate.binary_metrics(truth, predicted)
    assert r["precision"]["value"] == precision
    assert r["recall"]["value"] == recall
    assert r["f1"]["value"] == f1
    for metric in ("precision", "recall"):
        if r[metric]["denominator"] == 0:
            assert r[metric]["confidence_interval"] is None
    json.dumps(r, allow_nan=False)


@pytest.mark.parametrize("truth,predicted", [([], []), ([1], []), ([1], [0, 1]), ([2], [1]),
                                           ([1.0], [1]), ([1], [float("nan")]), (["1"], [1])])
def test_invalid_labels_or_lengths_are_rejected(truth, predicted):
    with pytest.raises(ValueError):
        evaluate.binary_metrics(truth, predicted)


@pytest.mark.parametrize("args", [(2, 1), (-1, 2), (1.5, 2), (True, 2), (1, -1), (1, 2, 1), (1, 2, float("nan"))])
def test_invalid_interval_parameters_are_rejected(args):
    with pytest.raises(ValueError):
        evaluate.wilson_interval(*args)


def _csv(path, rows, columns=evaluate.COLUMNS):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)


def _manifest(path, ids, sampling="random"):
    path.write_text(json.dumps({"manifest_version": "1", "sampling": sampling,
                                "items": [{"document_id": doc, "change_id": cid} for doc, cid in ids]}),
                    encoding="utf-8")
    return path


def test_multiple_documents_with_local_change_ids(tmp_path):
    path = tmp_path / "input.csv"
    _csv(path, [["p01", "c001", 1, 1], ["p01", "c002", 0, 1], ["p02", "c001", 1, 0]])
    ids = [("p01", "c001"), ("p01", "c002"), ("p02", "c001")]
    manifest = _manifest(tmp_path / "manifest.json", ids)
    r = evaluate.evaluate_csv(path, sampling="random", manifest=manifest)
    assert r["overall"]["counts"] == {"tn": 0, "fp": 1, "fn": 1, "tp": 1}
    assert r["overall"]["recall"]["value"] == 0.5
    assert r["per_document"]["p01"]["predicted_positive"] == 2
    assert r["per_document"]["p01"]["push_rate"]["value"] == 1
    assert r["per_document"]["p02"]["predicted_positive"] == 0
    assert r["overall"]["recall"]["confidence_interval"]["level"] == 0.95
    assert r["coverage"]["expected"] == r["coverage"]["observed"] == 3
    assert r["coverage"]["id_set_matches"] is True
    assert len(r["coverage"]["manifest_sha256"]) == 64
    _manifest(manifest, ids, "census")
    assert evaluate.evaluate_csv(path, sampling="census", manifest=manifest)["overall"]["recall"]["confidence_interval"] is None


@pytest.mark.parametrize("rows", [[], [["p01", "c001", "", 1]], [["p01", "c001", 1, ""]],
                                  [["p01", "c001", 1, 2]], [["", "c001", 1, 1]],
                                  [["p01", "", 1, 1]], [["p01", "c001", 1, 1], ["p01", "c001", 0, 0]]])
def test_bad_prediction_rows_are_rejected(tmp_path, rows):
    path = tmp_path / "input.csv"
    _csv(path, rows)
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001")])
    with pytest.raises(ValueError):
        evaluate.evaluate_csv(path, sampling="random", manifest=manifest)


def test_cli_json_is_aggregate_and_input_cannot_be_overwritten(tmp_path, capsys):
    path = tmp_path / "input.csv"
    _csv(path, [["p01", "c001", 1, 1], ["p01", "c002", 0, 0]])
    ids = [("p01", "c001"), ("p01", "c002")]
    manifest = _manifest(tmp_path / "manifest.json", ids)
    evaluate.main([str(path), "--sampling", "random", "--manifest", str(manifest)])
    result = json.loads(capsys.readouterr().out)
    assert result["overall"]["n"] == 2
    assert "c001" not in json.dumps(result)
    out = tmp_path / "output.json"
    _manifest(manifest, ids, "census")
    evaluate.main([str(path), "--sampling", "census", "--manifest", str(manifest), "--out", str(out)])
    assert json.loads(out.read_text())["sampling"] == "census"
    for target in (path, out, manifest):
        before = target.read_bytes()
        with pytest.raises(SystemExit):
            evaluate.main([str(path), "--sampling", "census", "--manifest", str(manifest), "--out", str(target)])
        assert target.read_bytes() == before


def test_stratified_samples_are_not_treated_as_equal_weight_random(tmp_path):
    path = tmp_path / "input.csv"
    _csv(path, [["p01", "c001", 1, 1]])
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001")], "stratified")
    with pytest.raises(ValueError, match="分层样本"):
        evaluate.evaluate_csv(path, sampling="stratified", manifest=manifest)
    # 不仅拒绝选项名：即便调用方声称 random / census，也必须尊重清单的实际设计。
    for sampling in ("random", "census"):
        with pytest.raises(ValueError, match="分层样本"):
            evaluate.evaluate_csv(path, sampling=sampling, manifest=manifest)


def test_missing_or_duplicate_csv_header_is_rejected(tmp_path):
    path = tmp_path / "input.csv"
    for columns in (("document_id", "change_id"), (*evaluate.COLUMNS, "change_id")):
        _csv(path, [], columns)
        with pytest.raises(ValueError):
            evaluate.read_predictions(path)


@pytest.mark.parametrize("sampling", ["census", "random"])
@pytest.mark.parametrize("actual,missing,extra", [
    ([("p01", "c001")], 2, 0),                         # 漏掉一行及整个文稿
    ([("p01", "c001"), ("p01", "c002")], 1, 0),       # 整个 p02 缺失
    ([("p01", "c001"), ("p01", "c002"), ("p02", "c999")], 1, 1),  # 总行数相同也不能冒充完整
    ([("p01", "c001"), ("p01", "c002"), ("p02", "c001"), ("p02", "c002")], 0, 1),
])
def test_exact_expected_id_set_required(tmp_path, sampling, actual, missing, extra):
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001"), ("p01", "c002"), ("p02", "c001")], sampling)
    predictions = tmp_path / "input.csv"
    _csv(predictions, [[doc, cid, 1, 1] for doc, cid in actual])
    with pytest.raises(ValueError, match=f"缺少 {missing} 条，多出 {extra} 条"):
        evaluate.evaluate_csv(predictions, sampling=sampling, manifest=manifest)


@pytest.mark.parametrize("design,claimed", [("census", "random"), ("random", "census")])
def test_declared_design_cannot_be_changed_by_option(tmp_path, design, claimed):
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001")], design)
    predictions = tmp_path / "input.csv"
    _csv(predictions, [["p01", "c001", 1, 1]])
    with pytest.raises(ValueError, match="不一致"):
        evaluate.evaluate_csv(predictions, sampling=claimed, manifest=manifest)


@pytest.mark.parametrize("column,value", [("stratum", "flagged"), ("weight", "2"),
                                           ("inclusion_probability", "0.2"), ("sampling", "stratified"),
                                           ("annotation_note", "不可静默丢弃")])
def test_extra_metadata_is_not_silently_dropped(tmp_path, column, value):
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001")])
    predictions = tmp_path / "input.csv"
    _csv(predictions, [["p01", "c001", 1, 1, value]], (*evaluate.COLUMNS, column))
    with pytest.raises(ValueError, match="分层、权重或其他额外列"):
        evaluate.evaluate_csv(predictions, sampling="random", manifest=manifest)


@pytest.mark.parametrize("data", [None, [], {}, {"manifest_version": "2", "sampling": "random", "items": []},
    {"manifest_version": "1", "sampling": "random", "items": []},
    {"manifest_version": "1", "sampling": "random", "items": "x"},
    {"manifest_version": "1", "sampling": "random", "items": [None]},
    {"manifest_version": "1", "sampling": "random", "items": [{"document_id": "p01"}]},
    {"manifest_version": "1", "sampling": "random", "items": [{"document_id": 1, "change_id": "c001"}]},
    {"manifest_version": "1", "sampling": "random", "items": [{"document_id": " ", "change_id": "c001"}]},
    {"manifest_version": "1", "sampling": "random", "items": [{"document_id": "p01", "change_id": "c001", "weight": 2}]},
])
def test_invalid_manifest_rejected(tmp_path, data):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluate.read_manifest(manifest)


def test_manifest_duplicate_id_and_malformed_json_rejected(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001"), (" p01 ", "c001")])
    with pytest.raises(ValueError, match="重复"):
        evaluate.read_manifest(manifest)
    manifest.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        evaluate.read_manifest(manifest)


def test_manifest_bom_and_whitespace_ids_and_row_order(tmp_path):
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001"), ("p02", "c001")], "census")
    manifest.write_text(manifest.read_text(), encoding="utf-8-sig")
    predictions = tmp_path / "input.csv"
    _csv(predictions, [[" p02 ", "c001", 0, 1], ["p01", " c001 ", 1, 0]])
    r = evaluate.evaluate_csv(predictions, sampling="census", manifest=manifest)
    assert r["overall"]["n"] == 2 and r["overall"]["recall"]["value"] == 0


def test_cli_requires_manifest_and_failure_produces_no_result(tmp_path, capsys):
    predictions, out = tmp_path / "input.csv", tmp_path / "out.json"
    _csv(predictions, [["p01", "c001", 1, 1]])
    with pytest.raises(SystemExit) as exc:
        evaluate.main([str(predictions), "--sampling", "census", "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    manifest = _manifest(tmp_path / "manifest.json", [("p01", "c001"), ("p01", "c002")], "census")
    with pytest.raises(SystemExit) as exc:
        evaluate.main([str(predictions), "--sampling", "census", "--manifest", str(manifest), "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    captured = capsys.readouterr()
    assert not captured.out and "缺少 1 条" in captured.err
