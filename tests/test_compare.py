"""消融必须逐 ID 配对，不能让净增量隐藏新漏检或更换参考答案。"""

import csv
import json
from pathlib import Path

import pytest

from experiments.compare import compare_schemes, main


@pytest.fixture
def inputs(tmp_path):
    rows = [("p1", "c1", 1, 0), ("p1", "c2", 1, 1), ("p1", "c3", 0, 0),
            ("p2", "c1", 0, 1), ("p2", "c2", 1, 0)]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"manifest_version": "1", "sampling": "census", "items": [
        {"document_id": doc, "change_id": cid} for doc, cid, *_ in rows]}))

    def write(name, values):
        path = tmp_path / name
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["document_id", "change_id", "needs_review", "predicted_review"])
            w.writerows(values)
        return path

    base = write("base.csv", rows)
    changed = [(doc, cid, truth, pred) for (doc, cid, truth, _), pred in zip(rows, [1, 0, 1, 0, 1])]
    full = write("full.csv", list(reversed(changed)))
    return {"base": base, "full": full}, manifest, rows, write


def test_paired_transitions_and_per_document(inputs):
    schemes, manifest, *_ = inputs
    r = compare_schemes(schemes, baseline="base", sampling="census", manifest=manifest)
    full = r["schemes"]["full"]
    assert full["vs_baseline"]["overall"] == {
        "risky_recovered": 2, "risky_lost": 1, "safe_newly_pushed": 1,
        "safe_no_longer_pushed": 1, "net_true_positive_change": 1, "net_push_change": 1}
    assert full["vs_baseline"]["per_document"]["p1"]["net_true_positive_change"] == 0
    assert full["vs_baseline"]["per_document"]["p2"]["net_push_change"] == 0
    assert full["overall"]["recall"]["value"] == pytest.approx(2 / 3)
    assert full["overall"]["recall"]["confidence_interval"] is None
    assert set(r["schemes"]["base"]["vs_baseline"]["overall"].values()) == {0}
    assert len(full["predictions_sha256"]) == 64
    assert '"c1"' not in json.dumps(r)


def test_comparison_uses_one_snapshot_of_each_input(inputs, monkeypatch):
    schemes, manifest, *_ = inputs
    expected = compare_schemes(schemes, baseline="base", sampling="census", manifest=manifest)
    read_bytes = Path.read_bytes
    inputs_to_replace = {manifest, *schemes.values()}

    def replace_after_read(path):
        raw = read_bytes(path)
        if path in inputs_to_replace:
            path.write_bytes(b"replaced")
            inputs_to_replace.remove(path)
        return raw

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    actual = compare_schemes(schemes, baseline="base", sampling="census", manifest=manifest)
    assert actual == expected
    assert not inputs_to_replace


def test_reference_label_mismatch_is_rejected(inputs):
    schemes, manifest, rows, write = inputs
    schemes["full"] = write("changed.csv", [(doc, cid, 1 - truth, pred) for doc, cid, truth, pred in rows])
    with pytest.raises(ValueError, match="参考标签不一致"):
        compare_schemes(schemes, baseline="base", sampling="census", manifest=manifest)


@pytest.mark.parametrize("missing", [True, False])
def test_all_schemes_require_exact_independent_manifest(inputs, missing):
    schemes, manifest, rows, write = inputs
    schemes["full"] = write("bad.csv", rows[:-1] if missing else rows + [("p3", "c9", 0, 0)])
    with pytest.raises(ValueError, match="ID 集不一致"):
        compare_schemes(schemes, baseline="base", sampling="census", manifest=manifest)


@pytest.mark.parametrize("names,baseline", [(["base"], "base"), (["base", "full"], "absent"),
                                          (["", "base"], "base"), ([" base", "base"], "base")])
def test_invalid_scheme_names(inputs, names, baseline):
    schemes, manifest, *_ = inputs
    with pytest.raises(ValueError):
        compare_schemes({n: schemes["base"] for n in names}, baseline=baseline, sampling="census", manifest=manifest)


def test_cli_random_intervals_no_overwrite_and_failures(inputs, tmp_path, capsys):
    schemes, manifest, *_ = inputs
    data = json.loads(manifest.read_text()); data["sampling"] = "random"
    manifest.write_text(json.dumps(data))
    args = ["--scheme", f"base={schemes['base']}", "--scheme", f"full={schemes['full']}",
            "--baseline", "base", "--sampling", "random", "--manifest", str(manifest)]
    main(args)
    result = json.loads(capsys.readouterr().out)
    assert result["schemes"]["full"]["overall"]["recall"]["confidence_interval"]["method"] == "wilson"
    out = tmp_path / "result.json"
    main(args + ["--out", str(out)])
    for target in (out, manifest, schemes["base"]):
        old = target.read_bytes()
        with pytest.raises(SystemExit):
            main(args + ["--out", str(target)])
        assert target.read_bytes() == old
    with pytest.raises(SystemExit):
        main(args + ["--scheme", f"base={schemes['full']}"])
    with pytest.raises(SystemExit):
        main(args + ["--scheme", "invalid"])
    assert not capsys.readouterr().out


def test_cli_empty_positive_denominator_and_module_invocation(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "pred.csv"
    path.write_text("document_id,change_id,needs_review,predicted_review\np,c,0,0\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"manifest_version": "1", "sampling": "census",
                                   "items": [{"document_id": "p", "change_id": "c"}]}))
    args = ["--scheme", f"a={path}", "--scheme", f"b={path}", "--baseline", "a",
            "--sampling", "census", "--manifest", str(manifest)]
    for command in ([sys.executable, "-m", "experiments.compare"], [sys.executable, "experiments/compare.py"]):
        run = subprocess.run(command + args, check=True, text=True, capture_output=True)
        assert json.loads(run.stdout)["schemes"]["b"]["overall"]["recall"]["value"] is None
