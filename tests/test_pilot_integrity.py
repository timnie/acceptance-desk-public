"""#22 审阅回归：错配输入必须拒算，自检引用必须唯一定位或经人工确认。"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pilot_integrity", ROOT / "experiments/pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
DOCUMENTS = {side: pilot.normalize((ROOT / f"examples/{side}.txt").read_text()) for side in ("original", "revised")}
SOURCE_ARGS = ["--original", str(ROOT / "examples/original.txt"), "--revised", str(ROOT / "examples/revised.txt")]


def _runs(report, paths, mapping=None):
    return pilot.selfcheck_runs(report, paths, mapping, DOCUMENTS)


@pytest.fixture
def inputs(tmp_path, capsys):
    pilot.main(["sheet", str(ROOT / "examples/original.txt"), str(ROOT / "examples/revised.txt"),
                "--name", "demo", "--out-dir", str(tmp_path), "--mode", "rules_only"])
    paths = {"report": tmp_path / "demo_report.json", "key": tmp_path / "demo_pilot_key.json",
             "a": tmp_path / "demo_pilot_A.csv", "b": tmp_path / "demo_pilot_B.csv"}
    for name in ("a", "b"):
        rows = pilot.read_sheet(paths[name])
        for cid, row in rows.items():
            row.update(label="stronger_claim" if cid == "c001" else "no_risk",
                       needs_review="1" if cid == "c001" else "0")
        pilot.write_csv(paths[name], pilot.SHEET_COLUMNS, list(rows.values()))
    capsys.readouterr()
    return paths


def _measure(paths, *extra):
    pilot.main(["measure", *(str(paths[k]) for k in ("report", "key", "a", "b")), *extra,
                *SOURCE_ARGS])


def _edit_json(path, edit):
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("case", ["seed", "count", "design", "fingerprint"])
def test_record_measurement_requires_registered_sampling_evidence(inputs, case, capsys):
    def mutate(key):
        if case == "seed": key["seed"] = True
        elif case == "count": key.update(original_sentences=1, changed_original_sentences=999)
        elif case == "design": key.pop("sampling_design")
        else: key.pop("report_sha256")
    _edit_json(inputs["key"], mutate)
    with pytest.raises(SystemExit) as exc:
        _measure(inputs)
    assert exc.value.code == 2 and "## 1." not in capsys.readouterr().out


def test_record_measurement_replays_sample_even_when_counts_still_match(tmp_path, capsys):
    o = "".join(f"第{i}组结果可能成立。" if i % 2 else f"第{i}组项目正在记录。" for i in range(40))
    r = "".join(f"第{i}组结果成立。" if i % 2 else f"第{i}组项目正在登记。" for i in range(40))
    (tmp_path / "o.txt").write_text(o); (tmp_path / "r.txt").write_text(r)
    pilot.main(["sheet", str(tmp_path / "o.txt"), str(tmp_path / "r.txt"), "--mode", "rules_only",
                "--name", "demo", "--out-dir", str(tmp_path), "-n", "8"])
    key = json.loads((tmp_path / "demo_pilot_key.json").read_text())
    report = json.loads((tmp_path / "demo_report.json").read_text())
    old = next(iter(key["items"]))
    new = next(c for c in report["changes"] if c["id"] not in key["items"]
               and c["review"]["flagged"] == key["items"][old]["flagged"])
    key["items"][new["id"]] = key["items"].pop(old)
    (tmp_path / "demo_pilot_key.json").write_text(json.dumps(key))
    for who in ("A", "B"):
        path = tmp_path / f"demo_pilot_{who}.csv"
        rows = pilot.read_sheet(path)
        row = rows.pop(old); row.update(change_id=new["id"])
        for side in ("original", "revised"):
            row[side] = new[side]["text"]
            for pos in ("start", "end"): row[f"{side}_{pos}"] = str(new[side][pos])
        rows[new["id"]] = row
        for row in rows.values(): row.update(label="no_risk", needs_review="0")
        pilot.write_csv(path, pilot.SHEET_COLUMNS, list(rows.values()))
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        pilot.main(["measure", *[str(tmp_path / n) for n in ("demo_report.json", "demo_pilot_key.json",
                                                           "demo_pilot_A.csv", "demo_pilot_B.csv")],
                    "--original", str(tmp_path / "o.txt"), "--revised", str(tmp_path / "r.txt")])
    captured = capsys.readouterr()
    assert exc.value.code == 2 and "## 1." not in captured.out
    assert "抽样结果不一致" in captured.err


def test_measure_valid_batch(inputs, capsys):
    _measure(inputs)
    out = capsys.readouterr().out
    assert "标注样本 8 条" in out and "高风险比例" in out


@pytest.mark.parametrize("case", ["a_missing", "both_missing", "empty", "duplicate", "blank_id",
                                 "a_text", "both_text", "offset", "missing_column"])
def test_measure_refuses_bad_sheets(inputs, case, capsys):
    names = ("a", "b") if case.startswith("both") else ("a",)
    for name in names:
        rows = list(pilot.read_sheet(inputs[name]).values())
        columns = pilot.SHEET_COLUMNS
        if case.endswith("missing"):
            rows.pop()
        elif case == "empty":
            rows = []
        elif case == "duplicate":
            rows.append(dict(rows[0]))
        elif case == "blank_id":
            rows[0]["change_id"] = " "
        elif case.endswith("text"):
            rows[0]["revised"] = "误用另一篇论文的句子。"
        elif case == "offset":
            rows[0]["original_start"] = "9999"
        else:
            columns = [c for c in columns if c != "needs_review"]
            rows = [{c: row[c] for c in columns} for row in rows]
        pilot.write_csv(inputs[name], columns, rows)
    with pytest.raises(SystemExit) as exc:
        _measure(inputs)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "[错误]" in captured.err and "## 1." not in captured.out


@pytest.mark.parametrize("case", ["hash", "missing_hash", "mode", "total", "population", "sampled", "flag", "unknown_id"])
def test_measure_refuses_bad_key(inputs, case, capsys):
    def mutate(key):
        if case == "hash":
            key["original_sha256"] = "0" * 64
        elif case == "missing_hash":
            del key["revised_sha256"]
        elif case == "mode":
            key["mode"] = "full"
        elif case == "total":
            key["total_changes"] += 1
        elif case in ("population", "sampled"):
            key["groups"]["flagged"][case] += 1
        elif case == "flag":
            key["items"]["c001"]["flagged"] = not key["items"]["c001"]["flagged"]
        else:
            key["items"]["c999"] = key["items"].pop("c001")
    _edit_json(inputs["key"], mutate)
    with pytest.raises(SystemExit) as exc:
        _measure(inputs)
    assert exc.value.code == 2 and "## 1." not in capsys.readouterr().out


def test_measure_refuses_missing_entire_unflagged_stratum(inputs, capsys):
    key = json.loads(inputs["key"].read_text())
    for name in ("a", "b"):
        rows = pilot.read_sheet(inputs[name])
        kept = [row for cid, row in rows.items() if key["items"][cid]["flagged"]]
        pilot.write_csv(inputs[name], pilot.SHEET_COLUMNS, kept)
    with pytest.raises(SystemExit):
        _measure(inputs)
    assert "筛选召回率" not in capsys.readouterr().out


@pytest.mark.parametrize("case", ["duplicate", "empty", "different_ids", "different_text"])
def test_kappa_refuses_corrupt_or_different_sheets(inputs, case):
    rows = list(pilot.read_sheet(inputs["a"]).values())
    if case == "duplicate":
        rows.append(dict(rows[0]))
    elif case == "empty":
        rows = []
    elif case == "different_ids":
        rows.pop()
    else:
        rows[0]["original"] = "不同批次的句子。"
    pilot.write_csv(inputs["a"], pilot.SHEET_COLUMNS, rows)
    with pytest.raises(SystemExit):
        pilot.main(["kappa", str(inputs["a"]), str(inputs["b"])])


def test_weighted_estimates_refuses_missing_gold(inputs):
    key = json.loads(inputs["key"].read_text())
    gold = {cid: {"wide": True} for cid in key["items"]}
    gold.pop(next(iter(gold)))
    with pytest.raises(ValueError, match="完整对应"):
        pilot.weighted_estimates(key, gold, "wide")


def _change(cid, original, revised):
    return {"id": cid, "original": {"text": original}, "revised": {"text": revised}}


def _hits(answer, changes):
    documents = {}
    for side in ("original", "revised"):
        full = ""
        for change in changes:
            target = change[side]
            target.update(start=len(full), end=len(full) + len(target["text"]))
            full += target["text"]
        documents[side] = full
    return pilot.selfcheck_hits(answer, changes, documents=documents)


def test_invented_opposite_quote_is_pending():
    changes = [_change("c001", "结果可能显著。", "结果十分显著。")]
    assert _hits("改句：结果并不显著。", changes) == (set(), ["改句：结果并不显著。"])


def test_duplicate_exact_quote_is_pending():
    changes = [_change("c001", "结果可能显著。", "结果显著。"),
               _change("c002", "结果不显著。", "结果显著。")]
    assert _hits("改句：结果显著。", changes) == (set(), ["改句：结果显著。"])


def test_complete_sentence_inside_split_or_merge_is_matched():
    changes = [_change("c001", "本研究采用问卷调查，并在三所高校收集数据。", "本研究采用问卷调查。数据在三所高校收集。")]
    assert _hits("改句：数据在三所高校收集。", changes) == ({"c001"}, [])
    assert _hits("改句：三所高校", changes)[0] == set()


def test_quote_covering_two_changes_is_pending():
    changes = [_change("c001", "甲可能有效。", "甲有效。"), _change("c002", "乙可能有效。", "乙有效。")]
    assert _hits("改句：甲有效。乙有效。", changes)[0] == set()


def test_deleted_quote_cannot_credit_a_rewritten_sentence():
    changes = [_change("c001", "原稿的句子。", "改稿的句子。")]
    assert _hits("删句：原稿的句子。", changes)[0] == set()


@pytest.fixture
def mapping_case(inputs, tmp_path):
    report = json.loads(inputs["report"].read_text())
    answer = tmp_path / "answer.txt"
    raw = "改句：模型没有原样抄写的引文。\n说明：需要人工核对位置。\n".encode()
    answer.write_bytes(raw)
    row = {"answer_sha256": hashlib.sha256(raw).hexdigest(),
           "report_sha256": pilot.report_digest(report),
           **{s + "_sha256": report["document"][s + "_sha256"] for s in ("original", "revised")},
           "quote_index": "1", "kind": "改句", "quote": "模型没有原样抄写的引文。",
           "change_id": "c001", "note": "人工核对原始回答上下文，对应 c001 的位置。"}
    path = tmp_path / "mapping.csv"
    pilot.write_csv(path, pilot.MAPPING_COLUMNS, [row])
    return report, answer, raw, row, path


def test_manual_mapping_is_used_by_both_commands_without_changing_original(inputs, mapping_case, capsys):
    report, answer, raw, row, mapping = mapping_case
    assert _runs(report, [answer])[0][1] == set()
    assert _runs(report, [answer], mapping)[0][1:] == ({"c001"}, [])
    # 两次原始回答完全相同时，可以共用由内容哈希绑定的同一份确认。
    assert len(_runs(report, [answer, answer], mapping)) == 2
    pilot.main(["selfcheck", str(inputs["report"]), str(answer), "--mapping", str(mapping), *SOURCE_ARGS])
    assert "c001" in capsys.readouterr().out
    _measure(inputs, "--selfcheck", str(answer), "--mapping", str(mapping))
    out = capsys.readouterr().out
    assert "点到 1 处修改" in out and "0 条待人工核对" in out
    assert answer.read_bytes() == raw


@pytest.mark.parametrize("field", ["answer_sha256", "report_sha256", "original_sha256", "quote_index", "quote", "kind", "change_id", "note"])
def test_stale_or_invalid_manual_mapping_is_rejected(mapping_case, field):
    report, answer, raw, row, mapping = mapping_case
    bad = dict(row)
    bad[field] = {"answer_sha256": "0" * 64, "report_sha256": "0" * 64, "original_sha256": "0" * 64, "quote_index": "2",
                  "quote": "不同引文", "kind": "删句", "change_id": "c999", "note": ""}[field]
    pilot.write_csv(mapping, pilot.MAPPING_COLUMNS, [bad])
    with pytest.raises(ValueError):
        _runs(report, [answer], mapping)
    assert answer.read_bytes() == raw


def test_duplicate_manual_mapping_is_rejected(mapping_case):
    report, answer, raw, row, mapping = mapping_case
    pilot.write_csv(mapping, pilot.MAPPING_COLUMNS, [row, row])
    with pytest.raises(ValueError, match="重复"):
        _runs(report, [answer], mapping)


def test_changed_raw_answer_invalidates_mapping(mapping_case):
    report, answer, raw, row, mapping = mapping_case
    answer.write_bytes(raw + "附加内容。".encode())
    with pytest.raises(ValueError, match="原始回答"):
        _runs(report, [answer], mapping)


def test_pending_template_can_be_filled_without_changing_answer(inputs, mapping_case, tmp_path):
    report, answer, raw, row, mapping = mapping_case
    template = tmp_path / "pending.csv"
    pilot.main(["selfcheck", str(inputs["report"]), str(answer), "--pending-out", str(template), *SOURCE_ARGS])
    import csv
    with template.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["quote_index"] == "1" and rows[0]["quote"] == row["quote"]
    assert rows[0]["change_id"] == rows[0]["note"] == ""
    rows[0].update(change_id="c001", note="人工核对了文稿位置")
    pilot.write_csv(template, pilot.MAPPING_COLUMNS, rows)
    assert _runs(report, [answer], template)[0][1] == {"c001"}
    assert answer.read_bytes() == raw
    # 再次导出不能覆盖已经填好的映射，也不能覆盖原始回答。
    for target in (template, answer, inputs["report"]):
        before = target.read_bytes()
        with pytest.raises(SystemExit):
            pilot.main(["selfcheck", str(inputs["report"]), str(answer), "--pending-out", str(target), *SOURCE_ARGS])
        assert target.read_bytes() == before


@pytest.mark.parametrize("kind,original,revised,quote", [
    ("改句", "结果可能显著。解释保持不变。\n\n结果显著。", "结果显著。解释保持不变。\n\n结果显著。", "结果显著。"),
    ("删句", "结果显著。说明甲。\n\n结果显著。说明乙。", "说明甲。\n\n结果显著。说明乙。", "结果显著。"),
])
def test_duplicate_in_unchanged_fulltext_is_pending(kind, original, revised, quote):
    report = pilot.audit(original, revised, mode="rules_only")
    documents = {"original": original, "revised": revised}
    # 修改列表里只有一处匹配，全文里另一处保持不变，仍必须判为有歧义。
    assert sum(quote in c["revised" if kind == "改句" else "original"]["text"] for c in report["changes"]) == 1
    assert pilot.selfcheck_hits(f"{kind}：{quote}", report["changes"], documents=documents) == (set(), [f"{kind}：{quote}"])


def test_without_fulltext_no_quote_is_automatically_credited():
    assert pilot.selfcheck_hits("改句：结果显著。", [_change("c001", "结果可能显著。", "结果显著。")])[0] == set()


@pytest.mark.parametrize("side", ["original", "revised"])
def test_selfcheck_rejects_wrong_fulltext_before_output(inputs, mapping_case, side, tmp_path, capsys):
    report, answer, raw, row, mapping = mapping_case
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("不属于该报告的文稿。")
    args = SOURCE_ARGS.copy()
    args[args.index("--" + side) + 1] = str(wrong)
    with pytest.raises(SystemExit):
        pilot.main(["selfcheck", str(inputs["report"]), str(answer), *args])
    assert capsys.readouterr().out == ""


def test_measure_requires_fulltext_when_comparing_selfcheck(inputs, mapping_case, capsys):
    report, answer, raw, row, mapping = mapping_case
    with pytest.raises(SystemExit):
        pilot.main(["measure", *(str(inputs[k]) for k in ("report", "key", "a", "b")), "--selfcheck", str(answer)])
    assert "## 1." not in capsys.readouterr().out


def test_source_newline_normalization_and_position_check(inputs):
    report = json.loads(inputs["report"].read_text())
    documents = dict(DOCUMENTS)
    pilot.check_documents(report, documents)
    report["changes"][0]["revised"]["start"] += 1
    with pytest.raises(ValueError, match="区间与全文不一致"):
        pilot.check_documents(report, documents)


def test_cli_accepts_crlf_sources(inputs, mapping_case, tmp_path):
    report, answer, raw, row, mapping = mapping_case
    args = []
    for side in ("original", "revised"):
        path = tmp_path / (side + "_crlf.txt")
        path.write_bytes(DOCUMENTS[side].replace("\n", "\r\n").encode())
        args += ["--" + side, str(path)]
    pilot.main(["selfcheck", str(inputs["report"]), str(answer), *args])


def test_manual_confirmation_can_locate_duplicate_fulltext(tmp_path):
    original = "结果可能显著。说明保持不变。\n\n结果显著。"
    revised = "结果显著。说明保持不变。\n\n结果显著。"
    report = pilot.audit(original, revised, mode="rules_only")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False))
    answer = tmp_path / "answer.txt"
    answer.write_text("改句：结果显著。")
    args = []
    for side, full in (("original", original), ("revised", revised)):
        source = tmp_path / f"{side}.txt"
        source.write_text(full)
        args += ["--" + side, str(source)]
    mapping = tmp_path / "mapping.csv"
    pilot.main(["selfcheck", str(path), str(answer), *args, "--pending-out", str(mapping)])
    import csv
    with mapping.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    rows[0].update(change_id=report["changes"][0]["id"], note="核对为第一段的改动，另一处未修改")
    pilot.write_csv(mapping, pilot.MAPPING_COLUMNS, rows)
    runs = pilot.selfcheck_runs(report, [answer], mapping, {"original": original, "revised": revised})
    assert runs[0][1:] == ({report["changes"][0]["id"]}, [])
