"""experiments/style_labels.py：文风细分标注的导入核对与 A/B 比较。只用编写的示例行，不含任何真实标注。"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("style_labels", ROOT / "experiments" / "style_labels.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)

NOT = "not_present"


def _write(path: Path, columns: list[str], rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # 和 Google 表格导出一样带 BOM
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return path


def _row(cid, o="no", r="no", status="done", subtypes=None, oe="", re_="", note=""):
    subtypes = subtypes or {}
    return ({"change_id": cid, "original": f"原{cid}。", "revised": f"改{cid}。",
             "original_style_issue": o, "revised_style_issue": r, "annotation_status": status},
            {"change_id": cid, **{s: subtypes.get(s, NOT if status == "done" else "") for s in sl.SUBTYPES},
             "original_evidence": oe, "revised_evidence": re_, "note": note})


def _sheets(tmp_path: Path, name: str, rows: list[tuple[dict, dict]], extra_col: bool = True) -> tuple[Path, Path]:
    jcols = ["change_id", "original", "revised", *sl.JUDGEMENT_COLUMNS] + (["context_hidden"] if extra_col else [])
    j = _write(tmp_path / f"{name}_j.csv", jcols, [dict(x, **({"context_hidden": "前后文"} if extra_col else {})) for x, _ in rows])
    s = _write(tmp_path / f"{name}_s.csv", ["change_id", *sl.SUBTYPE_COLUMNS], [y for _, y in rows])
    return j, s


def _units(tmp_path: Path, ids: list[str]) -> Path:
    return _write(tmp_path / "units.csv", ["change_id", "original", "revised"],
                  [{"change_id": i, "original": f"原{i}。", "revised": f"改{i}。"} for i in ids])


def test_blank_not_present_and_uncertain_stay_distinct(tmp_path):
    rows = [
        _row("u1", "no", "no"),                                                       # 检查过，六类都没有
        _row("u2", "no", "uncertain", subtypes={"empty_setup": "uncertain"}, re_="开头一句"),
        _row("u3", status=""),                                                        # 没检查
        _row("u4", status="needs_context"),
    ]
    data = sl.load_annotator(*_sheets(tmp_path, "a", rows))
    assert sl.check_rows(data) == ([], [])
    assert data["rows"]["u1"]["empty_setup"] == NOT
    assert data["rows"]["u2"]["empty_setup"] == "uncertain"
    assert data["rows"]["u3"]["empty_setup"] == sl.UNCHECKED
    assert sl.progress(data) == {"done": 2, "空白": 1, "needs_context": 1}
    assert sl.distribution(data, "empty_setup") == {NOT: 1, "uncertain": 1, "空白": 2}


def test_done_rows_must_be_complete_and_evidenced(tmp_path):
    rows = [
        _row("u1", "no", "yes", subtypes={"redundant_summary": "added"}),            # yes 但没有改稿证据
        _row("u2", "no", "no", subtypes={"empty_setup": ""}),                         # done 却留了空白
        _row("u3", "No ", "maybe"),                                                   # 非法取值
    ]
    errors, _ = sl.check_rows(sl.load_annotator(*_sheets(tmp_path, "a", rows)))
    assert any("u1" in e and "revised_evidence 是空的" in e for e in errors)
    assert any("u2" in e and "empty_setup" in e and "not_present" in e for e in errors)
    assert any("u3" in e and "revised_style_issue=“maybe”" in e for e in errors)
    assert not any("u3" in e and "original_style_issue" in e for e in errors)        # “No ” 规范化后合法


def test_warnings_do_not_judge_for_the_annotator(tmp_path):
    rows = [_row("u1", "yes", "no", oe="原稿套话"),                                   # 有问题但六类都没有
            _row("u2", "no", "no", subtypes={"formulaic_structure": "retained"})]     # 都 no 却有 retained
    errors, warnings = sl.check_rows(sl.load_annotator(*_sheets(tmp_path, "a", rows)))
    assert errors == [] and len(warnings) == 2


def test_structure_errors(tmp_path):
    j, s = _sheets(tmp_path, "a", [_row("u1"), _row("u2")])
    j2 = _write(tmp_path / "dup.csv", ["change_id", *sl.JUDGEMENT_COLUMNS],
                [{"change_id": "u1", "original_style_issue": "no", "revised_style_issue": "no", "annotation_status": "done"}] * 2)
    with pytest.raises(sl.StyleSheetError, match="重复"):
        sl.load_annotator(j2, s)
    j3 = _write(tmp_path / "short.csv", ["change_id", "original_style_issue", "revised_style_issue", "annotation_status"],
                [{"change_id": "u1", "original_style_issue": "no", "revised_style_issue": "no", "annotation_status": "done"}])
    with pytest.raises(sl.StyleSheetError, match="判断表与子类表"):
        sl.load_annotator(j3, s)
    bad = _write(tmp_path / "nocol.csv", ["change_id", "original_style_issue"], [{"change_id": "u1"}])
    with pytest.raises(sl.StyleSheetError, match="缺少列.*实际表头"):
        sl.load_annotator(bad, s)


def test_units_check_ids_and_text(tmp_path):
    data = sl.load_annotator(*_sheets(tmp_path, "a", [_row("u1"), _row("u2")]))
    assert sl.check_units(data, _units(tmp_path, ["u1", "u2"])) == []
    data["rows"]["u2"]["_revised_text"] = "被改过。"
    (tmp_path / "units.csv").unlink()
    errors = sl.check_units(data, _units(tmp_path, ["u1", "u2", "u3"]))
    assert any("只在单元表 ['u3']" in e for e in errors) and any("u2：revised" in e for e in errors)


def test_compare_only_counts_rows_done_by_both(tmp_path):
    a = sl.load_annotator(*_sheets(tmp_path, "a", [
        _row("u1", "no", "yes", subtypes={"redundant_summary": "added"}, re_="总之一句"),
        _row("u2", "no", "no"),
        _row("u3", "yes", "yes", subtypes={"formulaic_structure": "retained"}, oe="首先其次", re_="首先其次"),
        _row("u4", "no", "no"),
    ]))
    b = sl.load_annotator(*_sheets(tmp_path, "b", [
        _row("u1", "no", "yes", subtypes={"redundant_summary": "added"}, re_="总之"),
        _row("u2", "no", "uncertain", subtypes={"empty_setup": "uncertain"}, re_="开头"),
        _row("u3", "yes", "yes", subtypes={"formulaic_structure": "worsened"}, oe="首先", re_="首先其次最后"),
        _row("u4", status=""),                                                        # B 还没标
    ]))
    r = sl.compare(a, b)
    assert r["total"] == 4 and r["compared"] == 3
    assert r["fields"]["original_style_issue"]["agree"] == 3
    assert r["fields"]["revised_style_issue"] == {**r["fields"]["revised_style_issue"], "agree": 2, "n": 3,
                                                   "uncertain_a": 0, "uncertain_b": 1}
    assert r["fields"]["empty_setup"]["kappa"] == pytest.approx(0.0)
    assert r["fields"]["chat_format_residue"]["kappa"] is None                       # 两人都只填 not_present
    pairs = {(d["change_id"], d["field"], d["a"], d["b"]) for d in r["disagreements"]}
    assert pairs == {("u2", "revised_style_issue", "no", "uncertain"), ("u2", "empty_setup", NOT, "uncertain"),
                     ("u3", "formulaic_structure", "retained", "worsened")}
    assert not any(d["change_id"] == "u4" for d in r["disagreements"])


def test_cli_never_overwrites_and_reports_progress(tmp_path, capsys):
    aj, as_ = _sheets(tmp_path, "a", [_row("u1"), _row("u2", "no", "yes", subtypes={"empty_setup": "added"}, re_="x")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1"), _row("u2", status="")])
    before = {p: p.read_bytes() for p in (aj, as_, bj, bs)}
    units = _units(tmp_path, ["u1", "u2"])
    out = tmp_path / "dis.csv"
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs), "--units", str(units), "--disagreements", str(out)]) == 0
    text = capsys.readouterr().out
    assert "B：共 2 条｜done 1｜needs_context 0｜空白 1" in text and "两人都标 done 的条目：1 / 2" in text
    assert out.exists()
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs), "--disagreements", str(out)]) == 2   # 不覆盖
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs), "--disagreements", str(aj)]) == 2    # 不覆盖输入
    assert {p: p.read_bytes() for p in (aj, as_, bj, bs)} == before
    js = tmp_path / "a.json"
    assert sl.main(["check", str(aj), str(as_), "--name", "A", "--units", str(units), "--out", str(js)]) == 0
    assert '"_revised_text"' not in js.read_text(encoding="utf-8") and "不是内容高风险的参考答案" in js.read_text(encoding="utf-8")


def test_cli_refuses_to_compare_with_fill_errors(tmp_path, capsys):
    aj, as_ = _sheets(tmp_path, "a", [_row("u1", "no", "yes")])                       # yes 缺证据
    bj, bs = _sheets(tmp_path, "b", [_row("u1")])
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs)]) == 1
    assert "先修正填写问题" in capsys.readouterr().err


@pytest.mark.parametrize("line", ["u1,no,no,done,extra\n", "u1,no,no\n"])
def test_ragged_csv_is_rejected(tmp_path, line):
    path = tmp_path / "ragged.csv"
    path.write_text("change_id,original_style_issue,revised_style_issue,annotation_status\n" + line)
    with pytest.raises(sl.StyleSheetError, match="列数"):
        sl.read_table(path, sl.JUDGEMENT_COLUMNS)


def test_source_text_and_evidence_whitespace_preserved(tmp_path):
    j, s = _row("u1", " YES ", "no", oe="  原证据\n", note=" 备注 ")
    j["original"] = "  原u1。\n"
    data = sl.load_annotator(*_sheets(tmp_path, "a", [(j, s)]))
    assert data["rows"]["u1"]["original_evidence"] == "  原证据\n"
    assert data["rows"]["u1"]["note"] == " 备注 "
    assert data["rows"]["u1"]["original_style_issue"] == "yes"
    assert any("original 与单元表不同" in e for e in sl.check_units(data, _units(tmp_path, ["u1"])))


def _subtype_csv(path: Path, data_rows: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["change_id", *sl.SUBTYPE_COLUMNS])
        w.writerows(data_rows)
    return path


def test_reader_stays_strict_about_short_rows(tmp_path):
    """通用读取不替连接器形状放宽：行比表头短一格也按错位拒绝。"""
    path = _subtype_csv(tmp_path / "short.csv", [["u1", *["not_present"] * 6, "原证据", "改证据"]])
    with pytest.raises(sl.StyleSheetError, match="列数"):
        sl.read_table(path, sl.SUBTYPE_COLUMNS)


def test_pad_snapshot_fills_only_omitted_trailing_note_in_a_new_copy(tmp_path):
    rows = [["u1", *["not_present"] * 6, " 原证据 ", "改证据\n第二行"],          # 连接器省略了行末空 note
            ["u2", *["not_present"] * 6, "", "", "有备注"]]                       # 满列，照抄
    src = _subtype_csv(tmp_path / "A_evidence.csv", rows)
    before = src.read_bytes()
    out = tmp_path / "A_evidence.padded.csv"
    info = sl.pad_snapshot(src, out)
    assert src.read_bytes() == before                                              # 原快照不动
    assert info["padded_rows"] == 1 and info["padded_cells"] == {"note": 1} and info["rows"] == 2
    got = sl.read_table(out, sl.SUBTYPE_COLUMNS)
    assert got["u1"]["note"] == "" and got["u1"]["original_evidence"] == " 原证据 "    # 证据逐字保留
    assert got["u1"]["revised_evidence"] == "改证据\n第二行" and got["u2"]["note"] == "有备注"
    with pytest.raises(FileExistsError):                                           # 不覆盖已有副本
        sl.pad_snapshot(src, out)


@pytest.mark.parametrize("row, msg", [
    (["u1", *["not_present"] * 6, "原证据"], "只能补末尾的可选列"),                 # 缺的是证据列，不补
    (["u1", *["not_present"] * 6, "原证据", "改证据", "备注", "多一格"], "多 1 格"),
    (["u1", *["not_present"] * 5], "只能补末尾的可选列"),                          # 缺判断列，不补
])
def test_pad_snapshot_refuses_what_cannot_be_an_omitted_blank(tmp_path, row, msg):
    src = _subtype_csv(tmp_path / "s.csv", [row])
    with pytest.raises(sl.StyleSheetError, match=msg):
        sl.pad_snapshot(src, tmp_path / "out.csv")
    assert not (tmp_path / "out.csv").exists()


def test_cli_pad_snapshot_reports_hashes(tmp_path, capsys):
    src = _subtype_csv(tmp_path / "s.csv", [["u1", *["not_present"] * 6, "a", "b"]])
    assert sl.main(["pad-snapshot", str(src), str(tmp_path / "s.padded.csv")]) == 0
    out = capsys.readouterr().out
    assert "补齐 1 行" in out and "（未改动）" in out
    assert sl.main(["pad-snapshot", str(src), str(src)]) == 2                      # 不能写回原快照


def test_discussion_lists_both_sides_and_open_questions_without_picking(tmp_path):
    a = sl.load_annotator(*_sheets(tmp_path, "a", [
        _row("u1", "yes", "yes", subtypes={"unnecessary_defensiveness": "retained"}, oe="当然也要看到", re_="当然也要看到"),
        _row("u2", "no", "no"),
    ]))
    b = sl.load_annotator(*_sheets(tmp_path, "b", [_row("u1", "no", "no", note="正常让步"), _row("u2", "no", "no")]))
    md = sl.discussion_markdown(a, b, sl.compare(a, b))
    assert "## u1" in md and "## u2" not in md                        # 只列有分歧的单元
    assert "> 原u1。" in md and "> 改u1。" in md                       # 原改句原样给出
    assert "| unnecessary_defensiveness ⚑ | retained | not_present |" in md
    assert "当然也要看到" in md and "正常让步" in md                    # 两人各自的证据和备注都在
    assert md.count("   - 裁决：") == 3 and "A 判 `yes`，B 判 `no`" in md
    assert "不代任何一方选择" in md and "不是内容高风险答案" in md


def test_cli_writes_discussion_as_new_file_only(tmp_path):
    aj, as_ = _sheets(tmp_path, "a", [_row("u1", "no", "yes", subtypes={"empty_setup": "added"}, re_="开头")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1")])
    out = tmp_path / "discussion.md"
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs), "--discussion", str(out)]) == 0
    assert "## u1" in out.read_text(encoding="utf-8")
    assert sl.main(["compare", str(aj), str(as_), str(bj), str(bs), "--discussion", str(out)]) == 2


def test_direction_that_contradicts_side_judgement_is_flagged(tmp_path):
    rows = [_row("u1", "no", "yes", subtypes={"redundant_summary": "retained"}, re_="总之"),   # retained 意味着原稿也有
            _row("u2", "yes", "no", subtypes={"empty_setup": "removed"}, oe="开头")]          # removed 只在原稿：一致
    errors, warnings = sl.check_rows(sl.load_annotator(*_sheets(tmp_path, "a", rows)))
    assert errors == [] and len(warnings) == 1 and "u1" in warnings[0] and "original_style_issue=no" in warnings[0]


# ---------------------------------------------------------------- 裁决导入

def _pair(tmp_path):
    a = sl.load_annotator(*_sheets(tmp_path, "a", [
        _row("u1", "yes", "yes", subtypes={"unnecessary_defensiveness": "retained"}, oe="并非", re_="并非"),
        _row("u2", "no", "yes", subtypes={"empty_setup": "added"}, re_="下面说明"),
        _row("u3", "no", "no"),
    ]))
    b = sl.load_annotator(*_sheets(tmp_path, "b", [_row("u1", "no", "no"), _row("u2", "no", "no"), _row("u3", "no", "no")]))
    return a, b


def _fill(md: str, verdicts: list[str], reason: str = "理由") -> str:
    out, k = [], 0
    for line in md.split("\n"):
        if line == "   - 裁决：":
            line = f"   - 裁决：{verdicts[k]}"; k += 1
        elif line == "   - 理由：":
            line = f"   - 理由：{reason}"
        out.append(line)
    assert k == len(verdicts)
    return "\n".join(out)


def test_adjudication_merges_agreed_values_and_verdicts(tmp_path):
    a, b = _pair(tmp_path)
    md = sl.discussion_markdown(a, b, sl.compare(a, b))
    # u1 三处分歧（两侧 + 防御性），u2 两处（改稿侧 + empty_setup）；第二处故意选 B，最后一处选第三个值
    filled = _fill(md, ["yes", "no", "retained", "yes", "retained"])
    filled = filled.replace("| original_style_issue ⚑ | yes | no |", "| original_style_issue ⚑   |  yes  | no |")  # 编辑器重排
    out = sl.apply_adjudication(a, b, filled)
    assert [(i["change_id"], i["field"], i["verdict"], i["same_as"]) for i in out["items"]] == [
        ("u1", "original_style_issue", "yes", "A"), ("u1", "revised_style_issue", "no", "B"),
        ("u1", "unnecessary_defensiveness", "retained", "A"),
        ("u2", "revised_style_issue", "yes", "A"), ("u2", "empty_setup", "retained", "neither")]
    m = out["merged"]
    assert m["u3"]["adjudicated_fields"] == "" and m["u3"]["empty_setup"] == "not_present"     # 一致处沿用
    assert m["u2"]["original_style_issue"] == "no" and m["u2"]["empty_setup"] == "retained"
    # 合并后 u1 记 retained 却把改稿判 no、u2 记 retained 却把原稿判 no：只提醒，不改判
    assert len(out["warnings"]) == 2
    assert out["compare"]["fields"]["original_style_issue"]["agree"] == 2                      # 独立统计不变


@pytest.mark.parametrize("mutate, msg", [
    (lambda s: s.replace("   - 裁决：yes", "   - 裁决：maybe", 1), "不是合法取值"),
    (lambda s: s.replace("   - 理由：理由", "   - 理由：", 1), "没有写理由"),
    (lambda s: s.replace("有没有文风问题？", "是否有问题？", 1), "问题编号或原文"),
    (lambda s: s.replace("| original_style_issue ⚑ | yes | no |", "| original_style_issue ⚑ | no | no |"), "表内 A/B"),
    (lambda s: s.replace("## u2", "## u9"), "单元与重算的分歧单元不一致"),
])
def test_adjudication_refuses_inconsistent_or_incomplete_files(tmp_path, mutate, msg):
    a, b = _pair(tmp_path)
    filled = _fill(sl.discussion_markdown(a, b, sl.compare(a, b)), ["yes", "yes", "retained", "yes", "added"])
    with pytest.raises(sl.StyleSheetError, match=msg):
        sl.apply_adjudication(a, b, mutate(filled))


def test_cli_adjudicate_writes_new_files_and_records_inputs(tmp_path, capsys):
    aj, as_ = _sheets(tmp_path, "a", [_row("u1", "no", "yes", subtypes={"empty_setup": "added"}, re_="下面说明")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1")])
    a, b = sl.load_annotator(aj, as_), sl.load_annotator(bj, bs)
    filled = tmp_path / "filled.md"
    filled.write_text(_fill(sl.discussion_markdown(a, b, sl.compare(a, b)), ["yes", "added"]), encoding="utf-8")
    before = {p: p.read_bytes() for p in (aj, as_, bj, bs, filled)}
    rec, merged = tmp_path / "record.json", tmp_path / "merged.csv"
    args = ["adjudicate", str(aj), str(as_), str(bj), str(bs), str(filled), "--decided-by", "裁决人甲",
            "--record", str(rec), "--merged", str(merged)]
    assert sl.main(args) == 0
    out = capsys.readouterr().out
    assert "与 A 相同 2" in out and "不重新算 κ" in out
    import json
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert data["decided_by"] == "裁决人甲" and data["inputs"]["filled_discussion"]["file"] == "filled.md"
    assert "independent_agreement" in data and "不是内容高风险答案" in data["note"]
    assert sl.main(args) == 2                                                     # 不覆盖已有记录
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("field", ["original", "revised", "context_hidden"])
def test_compare_rejects_same_ids_from_different_text_batches(tmp_path, field):
    aj, ass = _sheets(tmp_path, "a", [_row("u1")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1")])
    a, b = sl.load_annotator(aj, ass), sl.load_annotator(bj, bs)
    key = "_context_before" if field == "context_hidden" else f"_{field}_text"
    if field == "context_hidden": a["rows"]["u1"][key] = "原有前文"
    b["rows"]["u1"][key] = "另一篇材料"
    with pytest.raises(sl.StyleSheetError, match="不同批次"):
        sl.compare(a, b)


def test_compare_without_text_requires_units_binding(tmp_path):
    a = sl.load_annotator(*_sheets(tmp_path, "a", [_row("u1")]))
    b = sl.load_annotator(*_sheets(tmp_path, "b", [_row("u1")]))
    for data in (a, b):
        data["rows"]["u1"].pop("_original_text")
        data["rows"]["u1"].pop("_revised_text")
    with pytest.raises(sl.StyleSheetError, match="--units"):
        sl.compare(a, b)
    assert sl.bind_units(a, b, _units(tmp_path, ["u1"]), "change_id") == []
    result = sl.compare(a, b)
    assert result["identity_check"] == "units_and_available_text"
    assert result["sources"]["units"]["sha256"]


def test_adjudication_rejects_unfinished_rows_without_outputs(tmp_path):
    aj, ass = _sheets(tmp_path, "a", [_row("u1"), _row("u2", status="")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1"), _row("u2", status="needs_context")])
    filled = tmp_path / "filled.md"; filled.write_text("")
    assert sl.main(["adjudicate", str(aj), str(ass), str(bj), str(bs), str(filled), "--decided-by", "测试人员",
                    "--record", str(tmp_path / "record.json"), "--merged", str(tmp_path / "merged.csv")]) == 2
    assert not (tmp_path / "record.json").exists() and not (tmp_path / "merged.csv").exists()


@pytest.mark.parametrize("command", ["compare", "adjudicate"])
def test_style_multi_output_conflict_leaves_no_partial_batch(tmp_path, command):
    aj, ass = _sheets(tmp_path, "a", [_row("u1", "no", "yes", subtypes={"empty_setup": "added"}, re_="虚构证据")])
    bj, bs = _sheets(tmp_path, "b", [_row("u1")])
    paths = [str(x) for x in (aj, ass, bj, bs)]
    first, last = tmp_path / "first.json", tmp_path / "existing.csv"
    last.write_text("keep")
    if command == "compare":
        args = ["compare", *paths, "--disagreements", str(first), "--discussion", str(last)]
    else:
        a, b = sl.load_annotator(aj, ass), sl.load_annotator(bj, bs)
        filled = tmp_path / "filled.md"
        filled.write_text(_fill(sl.discussion_markdown(a, b, sl.compare(a, b)), ["yes", "added"]))
        args = ["adjudicate", *paths, str(filled), "--decided-by", "测试人员", "--record", str(first), "--merged", str(last)]
    assert sl.main(args) == 2
    assert not first.exists() and last.read_text() == "keep"


def test_multiline_evidence_cannot_be_parsed_as_adjudication_structure(tmp_path):
    a, b = _pair(tmp_path)
    a["rows"]["u1"]["note"] = "首行\n## other_unit\n1. 证据里的编号\n   - 裁决：no\n   - 理由：只是原始备注"
    md = sl.discussion_markdown(a, b, sl.compare(a, b))
    filled = _fill(md, ["yes", "yes", "retained", "yes", "added"])
    result = sl.apply_adjudication(a, b, filled)
    assert len(result["items"]) == 5 and {i["change_id"] for i in result["items"]} == {"u1", "u2"}
    assert "> ## other_unit" in md and "> 1. 证据里的编号" in md
