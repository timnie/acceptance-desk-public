"""experiments/content_clues.py：已有内容判断线索的出处核对与覆盖统计。只用编写的示例。"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("content_clues", ROOT / "experiments" / "content_clues.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


@pytest.fixture
def files(tmp_path):
    a = _csv(tmp_path / "A.csv", ["change_id", "revised_evidence", "note"],
             [["u1", "保留原稿的证据强度", ""], ["u2", "", ""]])
    b = _csv(tmp_path / "B.csv", ["change_id", "revised_evidence", "note"],
             [["u1", "", "另：删去‘可能’可作论断强度审查线索"], ["u2", ""]])          # 行末空 note 被省略
    prep = tmp_path / "cases.md"
    prep.write_text("作者是否要将这一概括归于原作者？", encoding="utf-8")
    adj = tmp_path / "adj.json"
    adj.write_text(json.dumps({"items": [{"change_id": "u1", "reason": "宣告式表述"}]}, ensure_ascii=False), encoding="utf-8")
    units = tmp_path / "units.json"
    units.write_text(json.dumps({"units": [{"id": "u1", "change_ids": ["c001"]}, {"id": "u2", "change_ids": ["c002"]},
                                           {"id": "u3", "change_ids": ["c003"]}]}), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"changes": [
        {"id": f"c00{k}", "original": {"text": f"原{k}。"}, "revised": {"text": f"改{k}。"}} for k in (1, 2, 3)]},
        ensure_ascii=False), encoding="utf-8")
    return {"a": a, "b": b, "prep": prep, "adj": adj, "units": units, "report": report, "dir": tmp_path}


def _clues(files, clues):
    p = files["dir"] / "clues.json"
    p.write_text(json.dumps({"clues": clues}, ensure_ascii=False), encoding="utf-8")
    return p


def _args(files, clues_path, *extra):
    return [str(clues_path), "--a", str(files["a"]), "--b", str(files["b"]), "--adjudication", str(files["adj"]),
            "--preparer", str(files["prep"]), "--units", str(files["units"]), "--sample", str(files["a"]), *extra]


GOOD = [
    {"id": "k1", "unit": "u1", "source": {"kind": "annotator", "who": "B", "field": "note"},
     "quote": "删去‘可能’可作论断强度审查线索", "topic": "论断强度", "status": "pending"},
    {"id": "k2", "unit": "u1", "source": {"kind": "annotator", "who": "A", "field": "revised_evidence"},
     "quote": "保留原稿的证据强度", "topic": "论断强度", "status": "stated"},
    {"id": "k3", "unit": "u3", "source": {"kind": "preparer", "file": "cases.md"},
     "quote": "归于原作者", "topic": "归因", "status": "pending"},
]


def test_clues_traceable_and_coverage_split_by_layer(files, capsys):
    before = {k: files[k].read_bytes() for k in ("a", "b", "prep", "adj")}
    out = files["dir"] / "clues.md"
    assert cc.main(_args(files, _clues(files, GOOD), "--report", str(files["report"]), "--render", str(out))) == 0
    text = capsys.readouterr().out
    assert "样本 2 个单元：有线索 1（含待核实 1、含冲突 0、只有明确判断 0），未涉及 1" in text
    assert "完整分区 3 个单元：有线索 2" in text and "样本外有线索的单元（定向材料）：['u3']" in text
    md = out.read_text(encoding="utf-8")
    assert "## u1（样本内" in md and "## u3（样本外（定向）" in md and "> 改1。" in md
    assert "## 样本内未涉及内容的单元\n\nu2" in md and "不等于内容无风险" in md
    assert {k: files[k].read_bytes() for k in before} == before                    # 来源只读
    assert cc.main(_args(files, _clues(files, GOOD), "--report", str(files["report"]), "--render", str(out))) == 2


@pytest.mark.parametrize("bad, msg", [
    ({"quote": "删去可能"}, "必须逐字摘录"),                                       # 改写过的原话
    ({"source": {"kind": "annotator", "who": "A", "field": "note"}}, "必须逐字摘录"),   # 写错了出处
    ({"status": "gold"}, "status 必须是"),
    ({"unit": "u9"}, "不在分区里"),
    ({"source": {"kind": "adjudication"}, "quote": "删去‘可能’"}, "不在该单元的裁决理由里"),
])
def test_untraceable_or_invalid_clues_are_rejected(files, capsys, bad, msg):
    clue = {**GOOD[0], **bad}
    assert cc.main(_args(files, _clues(files, [clue]))) == 1
    assert msg in capsys.readouterr().err


def test_duplicate_ids_rejected(files, capsys):
    assert cc.main(_args(files, _clues(files, [GOOD[0], {**GOOD[1], "id": "k1"}]))) == 1
    assert "编号为空或重复" in capsys.readouterr().err


def test_adjudication_reason_can_be_a_source(files, capsys):
    clue = {"id": "k9", "unit": "u1", "source": {"kind": "adjudication"}, "quote": "宣告式", "status": "stated"}
    assert cc.main(_args(files, _clues(files, [clue]))) == 0


# ---------------------------------------------------------------- 作者回答

TEMPLATE = """# 问题清单

## 1. u1（样本内）

- [k1｜B] 删去‘可能’可作论断强度审查线索

- 结论：
- 是否需要作者看：

## 2. u3（样本外定向）

- [k3｜cases.md] 归于原作者

- 结论：
- 是否需要作者看：
"""


def _answered(template=TEMPLATE, a1="有可能削弱论证强度", n1="是", a3="没有依据，归因过强", n3="是"):
    return (template.replace("- 结论：\n- 是否需要作者看：\n\n## 2.", f"- 结论：{a1}\n- 是否需要作者看：{n1}\n\n## 2.")
            .rstrip("\n").rsplit("- 结论：", 1)[0] + f"- 结论：{a3}\n- 是否需要作者看：{n3}\n")


def _answer_files(files, answers, resolutions):
    d = files["dir"]
    (d / "q.md").write_text(TEMPLATE, encoding="utf-8")
    (d / "a.md").write_text(answers, encoding="utf-8")
    (d / "r.json").write_text(json.dumps({"resolutions": resolutions}, ensure_ascii=False), encoding="utf-8")
    return ["--resolutions", str(d / "r.json"), "--answers", str(d / "a.md"), "--template", str(d / "q.md")]


RES = [{"id": "r1", "unit": "u1", "clues": ["k1"], "quote": "有可能削弱论证强度", "needs_review": "是",
        "outcome": "needs_clarification"},
       {"id": "r2", "unit": "u3", "clues": ["k3"], "quote": "归因过强", "needs_review": "是", "outcome": "resolved"}]


def test_answers_import_reports_per_clue_outcomes(files, capsys):
    clues = GOOD + [{"id": "k4", "unit": "u3", "source": {"kind": "preparer", "file": "cases.md"},
                     "quote": "这一概括", "topic": "归因", "status": "pending"}]          # 没有被任何回答指到
    out = files["dir"] / "with_answers.md"
    extra = _answer_files(files, _answered(), RES)
    assert cc.main(_args(files, _clues(files, clues), *extra, "--report", str(files["report"]), "--render", str(out))) == 0
    text = capsys.readouterr().out
    assert "待核实线索 3 条的回应：需澄清 1，已回答 1，没有回答 1" in text
    assert "完整分区：含待核实线索的 2 个单元里，全部线索都已回答的 0 个" in text           # u3 还有 k4 没被回答
    md = out.read_text(encoding="utf-8")
    assert "作者回答（需澄清，需要作者看：是）：有可能削弱论证强度" in md
    assert "没有回答指到这一条（仍待核实）" in md


@pytest.mark.parametrize("answers, res, msg", [
    (_answered().replace("删去‘可能’", "删去可能"), RES, "与模板不同"),                     # 改了题
    (_answered(), [{**RES[0], "quote": "削弱"} , RES[1]], None),                             # 子串可以
    (_answered(), [{**RES[0], "quote": "归因过强"}, RES[1]], "不在该单元的回填段落里"),      # 引错单元
    (_answered(), [{**RES[0], "needs_review": "否"}, RES[1]], "needs_review 与该单元"),
    (_answered(), [{**RES[0], "clues": ["k2"]}, RES[1]], "原本不是待核实"),
    (_answered(), [{**RES[0], "outcome": "gold"}, RES[1]], "outcome 必须是"),
    (_answered(), [{**RES[0], "clues": []}, RES[1]], "没有指明"),
])
def test_answers_must_be_traceable(files, capsys, answers, res, msg):
    code = cc.main(_args(files, _clues(files, GOOD), *_answer_files(files, answers, res)))
    if msg is None:
        assert code == 0
    else:
        assert code == 1 and msg in capsys.readouterr().err


def test_answer_options_must_come_together(files, capsys):
    d = files["dir"]
    (d / "a.md").write_text(_answered(), encoding="utf-8")
    assert cc.main(_args(files, _clues(files, GOOD), "--answers", str(d / "a.md"))) == 2
    assert "必须同时给出" in capsys.readouterr().err


# ---------------------------------------------------------------- 补充回答与取代

SUPPLEMENT = "# 补充结论\n\n1. u1：表达本身变弱了。\n2. u3：这个是文风问题。\n"


def _with_supplement(files, resolutions):
    d = files["dir"]
    (d / "s.md").write_text(SUPPLEMENT, encoding="utf-8")
    return _answer_files(files, _answered(), resolutions) + ["--supplement", str(d / "s.md")]


def test_supplement_supersedes_earlier_status_but_keeps_it(files, capsys):
    res = RES + [{"id": "s1", "unit": "u1", "clues": ["k1"], "quote": "表达本身变弱了", "needs_review": None,
                  "outcome": "resolved", "source": "supplement", "supersedes": ["r1"]},
                 {"id": "s2", "unit": "u3", "clues": ["k3"], "quote": "文风问题", "needs_review": None,
                  "outcome": "other_reason", "source": "supplement", "supersedes": []}]
    out = files["dir"] / "v2.md"
    args = _with_supplement(files, res) + ["--report", str(files["report"]), "--render", str(out)]
    assert cc.main(_args(files, _clues(files, GOOD), *args)) == 0
    text = capsys.readouterr().out
    assert "待核实线索 2 条的回应：给了别的理由 1，已回答 1" in text                 # r1 的“需澄清”被取代
    md = out.read_text(encoding="utf-8")
    assert "作者回答，已被后续回答取代（需澄清" in md                                 # 早先的回答仍在清单里
    assert "补充回答（已回答，需要作者看：未写）：表达本身变弱了" in md


@pytest.mark.parametrize("change, msg", [
    ({"quote": "文风问题"}, "原话不在该单元的回填段落里"),                            # 引了别的单元那一行
    ({"needs_review": "是"}, "needs_review 应为 null"),                              # 补答没有这一栏，不推断
    ({"supersedes": ["r2"]}, "不是同一单元、同一线索"),
    ({"unit": "u2", "clues": []}, "补充回答里没有单元 u2"),
])
def test_supplement_entries_must_be_traceable(files, capsys, change, msg):
    entry = {"id": "s1", "unit": "u1", "clues": ["k1"], "quote": "表达本身变弱了", "needs_review": None,
             "outcome": "resolved", "source": "supplement", "supersedes": ["r1"], **change}
    assert cc.main(_args(files, _clues(files, GOOD), *_with_supplement(files, RES + [entry]))) == 1
    assert msg in capsys.readouterr().err


def test_supplement_entries_need_the_supplement_file(files, capsys):
    entry = {"id": "s1", "unit": "u1", "clues": ["k1"], "quote": "表达本身变弱了", "needs_review": None,
             "outcome": "resolved", "source": "supplement"}
    assert cc.main(_args(files, _clues(files, GOOD), *_answer_files(files, _answered(), RES + [entry]))) == 2
    assert "需要 --supplement" in capsys.readouterr().err


# ---------------------------------------------------------------- 复用已有五分类标签

def _labels(files, rows):
    return _csv(files["dir"] / "B_labels.csv", ["change_id", "label", "needs_review", "note"], rows)


def test_existing_labels_reused_and_only_real_conflicts_listed(files, capsys):
    res = [{**RES[0], "outcome": "resolved"}, RES[1]]                         # 作者确认 u1 有内容问题、需要看
    labels = _labels(files, [["u1", "no_risk", "0", ""], ["u2", "stronger_claim", "1", ""]])
    out = files["dir"] / "conflicts.csv"
    args = _answer_files(files, _answered(), res) + ["--labels", str(labels), "--conflicts", str(out)]
    assert cc.main(_args(files, _clues(files, GOOD), *args)) == 0
    text = capsys.readouterr().out
    assert "标注表 2 个单元全部有标签" in text and "与作者回答的冲突 2 处" in text
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert {r["kind"] for r in rows} == {"作者确认有内容问题，标签为 no_risk", "作者写需要看，标注者填不需要看"}
    assert all(r["unit"] == "u1" and r["裁决"] == "" for r in rows)


def test_category_difference_is_not_a_conflict(files, capsys):
    res = [{**RES[0], "outcome": "resolved"}, RES[1]]                         # 作者说过强，标签是语义篡改：都算有风险
    labels = _labels(files, [["u1", "semantic_change", "1", ""], ["u2", "no_risk", "0", ""]])
    args = _answer_files(files, _answered(), res) + ["--labels", str(labels)]
    assert cc.main(_args(files, _clues(files, GOOD), *args)) == 0
    assert "与作者回答的冲突 0 处" in capsys.readouterr().out


@pytest.mark.parametrize("rows, msg", [
    ([["u1", "no_risk", "0", ""]], "编号与样本不一致"),
    ([["u1", "语义篡改", "1", ""], ["u2", "no_risk", "0", ""]], "应为五个代码之一"),
    ([["u1", "no_risk", "maybe", ""], ["u2", "no_risk", "0", ""]], "needs_review 填的是"),
])
def test_label_sheet_must_match_sample_and_codes(files, capsys, rows, msg):
    args = _answer_files(files, _answered(), RES) + ["--labels", str(_labels(files, rows))]
    assert cc.main(_args(files, _clues(files, GOOD), *args)) == 1
    assert msg in capsys.readouterr().err


def test_labels_need_author_answers(files, capsys):
    labels = _labels(files, [["u1", "no_risk", "0", ""], ["u2", "no_risk", "0", ""]])
    assert cc.main(_args(files, _clues(files, GOOD), "--labels", str(labels))) == 2
    assert "需要同时给作者回答" in capsys.readouterr().err
