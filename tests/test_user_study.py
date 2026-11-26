"""只用虚构记录测试，空模板不能产生效果数字。"""

from collections import Counter
import csv
import json

import pytest

from experiments.user_study import COLUMNS, baseline_html, main, schedule, summarize


def write_records(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def records(tmp_path):
    rows = schedule()
    for row in rows:
        desk = row["tool"] == "desk"
        row.update(seconds="300" if desk else "600", end_reason="submitted", total_changes="25",
                   risky_found="4", false_rejections="1", probe_found="0")
        row["other_opened"] = "1" if desk else ""
        row["pushed_accepted_risky"] = "0" if desk else ""
    return tmp_path / "records.csv", rows


def test_six_person_design_balance_and_blank_metrics():
    rows = schedule()
    assert len(rows) == 12
    assert Counter((r["tool"], r["material_id"]) for r in rows) == {
        ("diff", "A"): 3, ("diff", "B"): 3, ("desk", "A"): 3, ("desk", "B"): 3}
    assert Counter(r["tool"] for r in rows if r["period"] == "1") == {"diff": 3, "desk": 3}
    for sid in {r["participant_id"] for r in rows}:
        selected = [r for r in rows if r["participant_id"] == sid]
        assert {r["material_id"] for r in selected} == {"A", "B"}
        assert {r["tool"] for r in selected} == {"diff", "desk"}
    assert all(not r[k] for r in rows for k in COLUMNS[4:])


def test_known_paired_result(records):
    path, rows = records
    write_records(path, list(reversed(rows)))
    result = summarize(path)
    assert result["participants"] == 6 and len(result["paired"]) == 6
    assert result["mean_paired_time_reduction"] == 0.5
    assert result["ratio_of_mean_time_reduction"] == 0.5
    assert result["by_tool"]["diff"]["found"] == result["by_tool"]["desk"]["found"] == 24
    assert result["by_tool"]["desk"]["discovery_rate"] == 0.8
    assert result["by_tool"]["desk"]["risky_exposures"] == 30
    assert all(r["system_missed"] == 1 and r["user_missed_pushed"] == 0 for r in result["trials"] if r["tool"] == "desk")
    assert len(result["source_sha256"]) == 64


def test_timeout_pending_is_not_counted_as_accepted(records):
    path, rows = records
    desk = next(r for r in rows if r["tool"] == "desk")
    desk.update(seconds="720", end_reason="timeout", risky_found="2", pushed_accepted_risky="1")
    write_records(path, rows)
    result = summarize(path)
    trial = next(r for r in result["trials"] if r["participant_id"] == desk["participant_id"] and r["tool"] == "desk")
    assert trial["user_missed_pushed"] == trial["pushed_unresolved_risky"] == 1
    assert result["by_tool"]["desk"]["timeouts"] == 1


@pytest.mark.parametrize("key,value", [("seconds", ""), ("seconds", "nan"), ("seconds", "inf"),
    ("seconds", "0"), ("seconds", "721"), ("end_reason", "timeout"), ("end_reason", "done"),
    ("risky_found", "6"), ("risky_found", "5"), ("risky_found", "-1"), ("risky_found", "1.5"),
    ("false_rejections", "21"), ("total_changes", "4"), ("total_changes", "26"),
    ("probe_found", "2"), ("other_opened", "2"), ("pushed_accepted_risky", "1"),
    ("pushed_accepted_risky", ""), ("tool", "diff"), ("material_id", "A")])
def test_bad_records_refuse_results(records, key, value):
    path, rows = records
    desk = next(r for r in rows if r["tool"] == "desk")
    desk[key] = value
    write_records(path, rows)
    with pytest.raises(ValueError):
        summarize(path)


def test_probe_requires_opened_and_diff_fields_are_blank(records):
    path, rows = records
    desk = next(r for r in rows if r["tool"] == "desk")
    desk.update(probe_found="1", other_opened="0")
    write_records(path, rows)
    with pytest.raises(ValueError, match="未展开"):
        summarize(path)
    desk.update(other_opened="1")
    diff = next(r for r in rows if r["tool"] == "diff")
    diff["pushed_accepted_risky"] = "0"
    write_records(path, rows)
    with pytest.raises(ValueError, match="应留空"):
        summarize(path)


@pytest.mark.parametrize("variant", ["missing", "duplicate", "blank", "extra_column", "short_row"])
def test_incomplete_or_duplicate_trials_refused(records, variant):
    path, rows = records
    if variant == "missing":
        rows.pop()
    elif variant == "duplicate":
        rows[-1] = rows[0]
    elif variant == "blank":
        rows = schedule()
    write_records(path, rows)
    if variant == "extra_column":
        path.write_text(path.read_text().replace("participant_id,", "participant_id,extra,", 1))
    if variant == "short_row":
        lines = path.read_text().splitlines(); lines[-1] = "S06,2"
        path.write_text("\n".join(lines))
    with pytest.raises(ValueError):
        summarize(path)


def test_baseline_escapes_content_and_shows_whole_document():
    page = baseline_html("<script>alert(1)</script>\r\n原文\n末段", "改文\n末段", '<img src=x onerror="alert(1)">')
    assert "<script>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page and "末段" in page
    assert "原稿" in page and "改稿" in page and "12 分钟" in page
    assert "original" in page and "revised" in page
    assert "https://" not in page and "<script " not in page
    for args in (("", "abc", "A"), ("abc", "", "A"), ("abc", "def", " ")):
        with pytest.raises(ValueError):
            baseline_html(*args)


def test_cli_outputs_no_overwrite_and_failure_no_result(records, tmp_path):
    path, rows = records
    template = tmp_path / "template.csv"
    main(["schedule", "--out", str(template)])
    with pytest.raises(SystemExit):
        main(["schedule", "--out", str(template)])
    out = tmp_path / "summary.json"
    with pytest.raises(SystemExit):
        main(["summarize", str(template), "--out", str(out)])
    assert not out.exists()
    write_records(path, rows)
    main(["summarize", str(path), "--out", str(out)])
    assert json.loads(out.read_text())["participants"] == 6
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        main(["summarize", str(path), "--out", str(path)])
    assert path.read_bytes() == before
    original, revised, page = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "diff.html"
    original.write_text("原句。"); revised.write_text("改句。")
    main(["baseline", str(original), str(revised), "--material-id", "A", "--out", str(page)])
    assert "普通 diff" in page.read_text()
