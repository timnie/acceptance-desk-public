"""审阅单元抽样与测量回归；全部为虚构文本与已知答案。"""

import copy
import json
from pathlib import Path

import pytest

from acceptance_desk import audit, review_units
from experiments import pilot, pilot_units

ORIGINAL = "开头不变。帕森斯把这个词译成铁笼。贝尔则强调外壳的意象。两种译名带来不同的读法。结尾不变。"
REVISED = "开头不变。帕森斯译成铁笼，贝尔强调外壳的意象，两种译名带来不同的读法。结尾不变。"


@pytest.fixture
def batch(tmp_path):
    report = audit(ORIGINAL, REVISED, mode="rules_only")
    units = review_units(report, ORIGINAL, REVISED)
    for name, text in (("o.txt", ORIGINAL), ("r.txt", REVISED)):
        (tmp_path / name).write_text(text, encoding="utf-8-sig")
    (tmp_path / "raw.json").write_text(json.dumps(report, ensure_ascii=False))
    partition = {"report_sha256": pilot.report_digest(report), "units": units}
    (tmp_path / "units.json").write_text(json.dumps(partition))
    pilot.main(["sheet", str(tmp_path / "o.txt"), str(tmp_path / "r.txt"),
                "--report", str(tmp_path / "raw.json"), "--unit-map", str(tmp_path / "units.json"),
                "--out-dir", str(tmp_path), "--name", "demo"])
    for who in ("A", "B"):
        path = tmp_path / f"demo_pilot_{who}.csv"
        rows = pilot.read_sheet(path)
        for row in rows.values():
            row.update(label="no_risk", needs_review="0")
        pilot.write_csv(path, pilot.SHEET_COLUMNS, list(rows.values()))
    key = json.loads((tmp_path / "demo_pilot_key.json").read_text())
    return tmp_path, report, key


def measure_args(path):
    return ["measure", *[str(path / n) for n in ("demo_report.json", "demo_pilot_key.json",
                                                "demo_pilot_A.csv", "demo_pilot_B.csv")],
            "--original", str(path / "o.txt"), "--revised", str(path / "r.txt")]


def test_unit_measurement_preserves_raw_report_and_counts_once(batch, capsys):
    path, report, key = batch
    assert len(report["changes"]) > key["total_changes"] == 1
    assert key["items"]["u001"]["change_ids"] == key["units"][0]["change_ids"]
    raw_before = (path / "demo_report.json").read_bytes()
    answer = path / "answer.txt"
    answer.write_text("改句：" + REVISED[len("开头不变。"): -len("结尾不变。")])
    pilot.main(measure_args(path) + ["--selfcheck", str(answer)])
    out = capsys.readouterr().out
    assert "测量单位：审阅单元" in out and "点到 1 处修改" in out
    assert (path / "demo_report.json").read_bytes() == raw_before
    assert pilot_units.unit_hits(set(key["units"][0]["change_ids"]), key["units"]) == {"u001"}


@pytest.mark.parametrize("case", ["report", "missing", "duplicate", "unknown", "sample_members", "design", "raw_total", "flag", "text", "sentence_count", "seed"])
def test_unit_measurement_refuses_stale_or_corrupt_input(batch, case, capsys):
    path, report, key = batch
    capsys.readouterr()
    if case == "report":
        key["report_sha256"] = "0" * 64
    elif case == "missing":
        key["units"][0]["change_ids"].pop()
    elif case == "duplicate":
        key["units"][0]["change_ids"] *= 2
    elif case == "unknown":
        key["units"][0]["change_ids"][0] = "c999"
    elif case == "sample_members":
        key["items"]["u001"]["change_ids"] = []
    elif case == "design":
        key["sampling_design"] = "manual"
    elif case == "raw_total":
        key["total_raw_changes"] = True
    elif case == "flag":
        key["items"]["u001"]["flagged"] = False
    elif case == "sentence_count":
        key["original_sentences"] = 999
    elif case == "seed":
        key["seed"] = True
    else:
        p = path / "demo_pilot_A.csv"
        rows = pilot.read_sheet(p)
        rows["u001"]["original"] += "错稿"
        pilot.write_csv(p, pilot.SHEET_COLUMNS, list(rows.values()))
    (path / "demo_pilot_key.json").write_text(json.dumps(key))
    with pytest.raises(SystemExit) as exc:
        pilot.main(measure_args(path))
    assert exc.value.code == 2 and "## 1." not in capsys.readouterr().out


def test_existing_annotation_batch_is_never_overwritten(batch):
    path, _, _ = batch
    before = {p: p.read_bytes() for p in path.glob("demo_*")}
    with pytest.raises(SystemExit):
        pilot.main(["sheet", str(path / "o.txt"), str(path / "r.txt"), "--out-dir", str(path),
                    "--name", "demo", "--mode", "rules_only"])
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("n", [0, -1, True])
def test_invalid_sample_size_is_rejected(n):
    with pytest.raises(ValueError, match="正整数"):
        pilot.sample_changes([], n, seed=1)


def test_nonadjacent_members_show_omission_instead_of_other_units_text():
    original, revised = "甲可能成立。乙可能成立。丙可能成立。", "甲成立。乙成立。丙成立。"
    report = audit(original, revised, mode="rules_only")
    assert len(report["changes"]) == 3
    view = pilot_units.measurement_view(report, {"original": original, "revised": revised},
                                       [{"id": "u1", "change_ids": ["c001", "c003"]},
                                        {"id": "u2", "change_ids": ["c002"]}])
    assert view["changes"][0]["original"]["text"] == "甲可能成立。\n[…其他单元…]\n丙可能成立。"


def test_partial_batch_conflict_leaves_existing_key_and_no_partial_tables(tmp_path):
    o, r = tmp_path / "o.txt", tmp_path / "r.txt"
    o.write_text(ORIGINAL); r.write_text(REVISED)
    key = tmp_path / "demo_pilot_key.json"
    key.write_text("previous annotations")
    with pytest.raises(SystemExit):
        pilot.main(["sheet", str(o), str(r), "--mode", "rules_only", "--name", "demo", "--out-dir", str(tmp_path)])
    assert key.read_text() == "previous annotations"
    assert not (tmp_path / "demo_pilot_A.csv").exists()
    assert not (tmp_path / "demo_pilot_B.csv").exists()


def test_full_partition_of_an_incomplete_report_is_rejected():
    from acceptance_desk.report import _summary
    original, revised = "甲可能成立。乙可能成立。", "甲成立。乙成立。"
    report = audit(original, revised, mode="rules_only")
    report["changes"].pop()
    report["summary"] = _summary(report["changes"])
    with pytest.raises(ValueError, match="修改与重新对齐"):
        pilot_units.measurement_view(report, {"original": original, "revised": revised},
                                     [{"id": "u001", "change_ids": ["c001"]}])
