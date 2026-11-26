"""#18 合并终稿与可读审阅记录（acceptance_desk/export.py）。只用 rules_only。"""

from __future__ import annotations

import copy
import random
from pathlib import Path

import pytest

from acceptance_desk import audit
from acceptance_desk import ReviewDataError, check_reviewed_report, validate_report
from acceptance_desk.export import (MergeError, PendingDecisions, decision_errors, document_segments,
                                    layout_differences, merge_decisions, review_record_markdown,
                                    visible_whitespace)
from acceptance_desk.text import normalize

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
NOW = "2026-11-10T21:00:00+08:00"


def _audit(o, r):
    return audit(o, r, mode="rules_only")


def _decide(rep, status_by_id=None, default="accepted", edits=None):
    """返回新报告：按 status_by_id 设置决定，其余用 default；edits 给 edited 条目的文本。"""
    rep = copy.deepcopy(rep)
    status_by_id, edits = status_by_id or {}, edits or {}
    for c in rep["changes"]:
        st = status_by_id.get(c["id"], default)
        ft = {"accepted": c["revised"]["text"], "rejected": c["original"]["text"], "pending": None,
              "edited": edits.get(c["id"])}[st]
        c["decision"].update(status=st, final_text=ft, decided_by=None if st == "pending" else "user",
                             decided_at=None if st == "pending" else NOW)
    return rep


def _merge_all(o, r, status, **kw):
    return merge_decisions(_decide(_audit(o, r), default=status), o, r, **kw).text


PAIRS = [
    ("examples-txt", (EX / "original.txt").read_text(encoding="utf-8"), (EX / "revised.txt").read_text(encoding="utf-8")),
    ("examples-md", (EX / "original.md").read_text(encoding="utf-8"), (EX / "revised.md").read_text(encoding="utf-8")),
    ("replace", "A。B。C。", "A。X。C。"),
    ("insert-para", "甲。\n\n乙。\n", "甲。\n\n新句子。\n\n乙。\n"),
    ("delete-para", "甲。\n\n删掉的一句。\n\n乙。\n", "甲。\n\n乙。\n"),
    ("delete-first", "开头一句话。正文第一句。正文第二句。", "正文第一句。正文第二句。"),
    ("insert-last", "正文第一句。正文第二句。", "正文第一句。正文第二句。\n\n总的来说，意义重大。"),
    ("split", "本研究采用问卷调查，并在两所高校收集数据。其余不变。", "本研究采用问卷调查。数据在两所高校收集。其余不变。"),
    ("merge", "本研究采用问卷调查。数据在两所高校收集。其余不变。", "本研究采用问卷调查，并在两所高校收集数据。其余不变。"),
    ("duplicates", "同一句。不同甲。同一句。同一句。", "同一句。不同乙。同一句。同一句。"),
    ("dup-delete", "同一句。同一句。同一句。", "同一句。同一句。"),
    ("whitespace-near-change", "A 句。\n\nB 句。\n", "A 句。\nX 句。   \n"),
    ("crlf", "第一句。\r\n\r\n第二句可能成立。\r\n", "第一句。\n\n第二句成立。\n"),
    ("only-whitespace-sentence", "结论 成立。", "结论成立。"),
    ("empty-original", "", "全新的一段。第二句。"),
    ("empty-revised", "将被删光的一段。第二句。", ""),
    ("both-empty", "", ""),
    ("identical", "完全相同。\n\n两段。", "完全相同。\n\n两段。"),
    ("para-merge", "甲句。\n\n乙句。", "甲句。\n乙句。"),
    ("para-split", "甲句。乙句。丙句。", "甲句。\n\n乙句。丙句。"),
    ("leading-trailing", "\n\n  开头。\n结尾。  \n\n", "开头。\n改过的结尾。"),
]


@pytest.mark.parametrize("name, o, r", PAIRS, ids=[p[0] for p in PAIRS])
def test_all_accept_is_revised_all_reject_is_original(name, o, r):
    assert _merge_all(o, r, "accepted") == normalize(r)
    assert _merge_all(o, r, "rejected") == normalize(o)


@pytest.mark.parametrize("name, o, r", PAIRS, ids=[p[0] for p in PAIRS])
def test_pending_policies_mark_draft(name, o, r):
    rep = _audit(o, r)
    if not rep["changes"]:
        res = merge_decisions(rep, o, r)
        assert not res.draft and res.text == normalize(r)
        return
    with pytest.raises(PendingDecisions) as e:
        merge_decisions(rep, o, r)
    assert e.value.ids == [c["id"] for c in rep["changes"]] and f"{len(rep['changes'])} 处待定" in str(e.value)
    a = merge_decisions(rep, o, r, pending="revised")
    b = merge_decisions(rep, o, r, pending="original")
    assert a.draft and b.draft and a.text == normalize(r) and b.text == normalize(o)
    assert a.counts["pending"] == len(rep["changes"])


def test_mixed_decisions_follow_final_text():
    o, r = "A。B。C。D。", "A。X。C。Y。"
    rep = _audit(o, r)
    ids = [c["id"] for c in rep["changes"]]
    got = merge_decisions(_decide(rep, {ids[0]: "accepted", ids[1]: "rejected"}), o, r).text
    assert got == "A。X。C。D。"
    got = merge_decisions(_decide(rep, {ids[0]: "edited", ids[1]: "edited"}, edits={ids[0]: "乙。", ids[1]: ""}), o, r)
    assert got.text == "A。乙。C。" and not got.draft
    assert got.counts["edited"] == 2


def test_edited_empty_removes_sentence_and_its_whitespace():
    o, r = "甲。\n\n删掉的一句。\n\n乙。\n", "甲。\n\n改过的一句。\n\n乙。\n"
    rep = _audit(o, r)
    (c,) = rep["changes"]
    res = merge_decisions(_decide(rep, {c["id"]: "edited"}, edits={c["id"]: ""}), o, r)
    assert res.text == "甲。\n\n乙。\n"


def test_insert_and_delete_rejections_in_paragraphs():
    o = (EX / "original.txt").read_text(encoding="utf-8")
    r = (EX / "revised.txt").read_text(encoding="utf-8")
    rep = _audit(o, r)
    ins = next(c for c in rep["changes"] if c["alignment"] == "0-1")
    dele = next(c for c in rep["changes"] if c["alignment"] == "1-0")
    # 只拒绝新增：终稿 = 改稿去掉新增句
    res = merge_decisions(_decide(rep, {ins["id"]: "rejected"}), o, r).text
    assert ins["revised"]["text"] not in res and res.replace("\n", "") == r.replace(ins["revised"]["text"], "").replace("\n", "")
    # 只保留被删的句子：原句回到终稿，且只出现一次
    res = merge_decisions(_decide(rep, {dele["id"]: "rejected"}), o, r).text
    assert res.count(dele["original"]["text"]) == 1


def test_duplicate_sentences_use_positions():
    o, r = "同一句。不同甲。同一句。", "同一句。不同乙。同一句。同一句。"
    rep = _audit(o, r)
    res = merge_decisions(_decide(rep, default="rejected"), o, r)
    assert res.text == o and res.text.count("同一句。") == 2
    assert merge_decisions(_decide(rep), o, r).text.count("同一句。") == 3


def test_layout_gap_counted_without_changes():
    o, r = "甲句。 乙句。\n", "甲句。乙句。"   # 只有句间空白和末尾换行不同
    rep = _audit(o, r)
    assert rep["changes"] == []
    res = merge_decisions(rep, o, r)
    assert res.text == r and res.layout_gaps == 2


@pytest.mark.parametrize("seed", range(300))
def test_random_documents_round_trip(seed):
    rng = random.Random(seed)
    pool = ["研究表明二者相关。", "样本量较小。", "结论有待验证。", "数据来自问卷。", "可能存在偏差。",
            "总的来说意义重大。", "实验进行了三轮。", "同一句。", "同一句。", "有效率为 92.5%。"]
    seps = ["", " ", "\n", "\n\n", "\n\n\n", "  \n"]

    def doc(n):
        return "".join(rng.choice(pool) + rng.choice(seps) for _ in range(n))

    o = doc(rng.randint(0, 8))
    words = [s for s in o.replace("\n", "|").split("|")]
    r = o
    for _ in range(rng.randint(0, 4)):  # 随机替换 / 插入 / 删除 / 改空白
        op = rng.choice(["rep", "ins", "del", "ws"])
        if op == "rep" and r:
            a = rng.choice(pool)
            r = r.replace(a, rng.choice(pool), 1)
        elif op == "ins":
            k = rng.randint(0, len(r))
            r = r[:k] + rng.choice(seps) + rng.choice(pool) + r[k:]
        elif op == "del" and r:
            a = rng.choice(pool)
            r = r.replace(a, "", 1)
        else:
            r = r.replace(rng.choice(seps) or "\n", rng.choice(seps), 1)
    del words
    assert _merge_all(o, r, "accepted") == normalize(r)
    if _audit(o, r)["changes"]:
        assert _merge_all(o, r, "rejected") == normalize(o)
    else:  # 没有修改（只差空白）时没有可保留的条目，版式跟随改稿，并报告空白差异
        res = merge_decisions(_audit(o, r), o, r)
        assert res.text == normalize(r) and (res.layout_gaps > 0) == (normalize(o) != normalize(r))


# ---------------------------------------------------------------- 拒绝不安全的输入

def test_wrong_document_rejected():
    o, r = "A。B。", "A。X。"
    rep = _decide(_audit(o, r))
    with pytest.raises(MergeError, match="改稿.*不是同一份"):
        merge_decisions(rep, o, "A。Y。")
    with pytest.raises(MergeError, match="原稿.*不是同一份"):
        merge_decisions(rep, "A。C。", r)


def test_tampered_report_rejected():
    o, r = "A。B。C。", "A。X。C。"
    rep = _decide(_audit(o, r))
    bad = copy.deepcopy(rep)
    bad["changes"][0]["original"]["start"] = 0
    with pytest.raises(MergeError, match="位置与文本不符"):
        merge_decisions(bad, o, r)
    bad = copy.deepcopy(rep)
    bad["changes"].append(copy.deepcopy(bad["changes"][0]))
    with pytest.raises(MergeError, match="编号重复"):
        merge_decisions(bad, o, r)
    bad = copy.deepcopy(rep)
    bad["changes"] = []
    with pytest.raises(MergeError, match="重新对齐的结果不一致"):
        merge_decisions(bad, o, r)


@pytest.mark.parametrize("dec, msg", [
    ({"status": "accepted", "final_text": "被偷改"}, "与改句不一致"),
    ({"status": "rejected", "final_text": "A。"}, "与原句不一致"),
    ({"status": "edited", "final_text": None}, "缺少最终文本"),
    ({"status": "pending", "final_text": "x"}, "待定条目不应有最终文本"),
    ({"status": "approved", "final_text": "x"}, "未知的决定状态"),
])
def test_illegal_decisions_rejected(dec, msg):
    o, r = "A。B。C。", "A。X。C。"
    rep = _decide(_audit(o, r))
    rep["changes"][0]["decision"].update(dec)
    assert any(msg in e for e in decision_errors(rep["changes"][0], trained=False))
    with pytest.raises(MergeError, match=msg):
        merge_decisions(rep, o, r, pending="revised")


def test_bad_policy():
    with pytest.raises(ValueError):
        merge_decisions(_audit("A。", "B。"), "A。", "B。", pending="skip")


# ---------------------------------------------------------------- 可读审阅记录

def test_record_markdown_complete_and_draft():
    o = (EX / "original.txt").read_text(encoding="utf-8")
    r = (EX / "revised.txt").read_text(encoding="utf-8")
    rep = _audit(o, r)
    ids = [c["id"] for c in rep["changes"]]
    rep = _decide(rep, {ids[0]: "rejected", ids[1]: "edited", ids[2]: "pending"}, edits={ids[1]: "含 ``` 反引号的改写。"})
    rep["changes"][0]["decision"]["note"] = "原文只说相关"
    res = merge_decisions(rep, o, r, pending="original")
    md = review_record_markdown(rep, res, title="示例", names={"original": "原稿.txt", "revised": "改稿.txt"},
                                formats={"original": "txt", "revised": "txt"}, now=NOW)
    assert md.startswith("# 审阅记录（草稿）：示例")
    assert "还有 1 处待定" in md and "暂按“保留原句”" in md
    assert "| 采用改句 | 5 |" in md and "| 保留原句 | 1 |" in md and "| 自行编辑 | 1 |" in md and "| 待定 | 1 |" in md
    assert "备注：原文只说相关" in md and "原稿.txt，txt" in md
    assert "````text\n含 ``` 反引号的改写。\n````" in md      # 围栏比文本里的反引号长
    assert "nli" not in md.lower()
    done = merge_decisions(_decide(rep, default="accepted"), o, r)
    md2 = review_record_markdown(_decide(rep, default="accepted"), done, title="示例", now=NOW)
    assert md2.startswith("# 审阅记录（完成版）：示例") and "草稿" not in md2


# ---------------------------------------------------------------- 已审阅记录的统一校验

def _two():
    o, r = "A。B。C。", "A。X。C。"
    return o, r, _decide(_audit(o, r))


@pytest.mark.parametrize("patch, msg", [
    (lambda c: c["decision"].update(status="auto_passed", decided_by="system"), "分类器尚未训练"),
    (lambda c: c["decision"].update(decided_by=None), "决定人应为 user"),
    (lambda c: c["decision"].update(decided_at=None), "缺少决定时间"),
    (lambda c: c["decision"].update(decided_at="昨天"), "时间格式不对"),
    (lambda c: c["decision"].pop("note"), "备注应为文本"),
    (lambda c: c.update(id=""), "缺少编号"),
    (lambda c: c["original"].update(start=True), "不能是 true/false"),
    (lambda c: c["original"].update(start=-1), "越界"),
    (lambda c: c["revised"].update(end=10**6), "越界"),
    (lambda c: c.update(original="A。"), "原句缺少文本"),
    (lambda c: c.update(alignment="3-3"), "未知的对齐类型"),
    (lambda c: c.pop("decision"), "缺少 decision"),
    (lambda c: c.update(review=None), "缺少 review"),
])
def test_reviewed_record_errors_are_domain_errors(patch, msg):
    o, r, rep = _two()
    patch(rep["changes"][0])
    with pytest.raises(ReviewDataError, match=msg):
        check_reviewed_report(rep, o, r)
    with pytest.raises(MergeError, match=msg):   # 合并沿用同一套规则
        merge_decisions(rep, o, r, pending="revised")


@pytest.mark.parametrize("broken", [None, [], {"document": {}}, {"document": {}, "changes": "x"},
                                    {"document": {"original_sha256": 1}, "changes": [1]}])
def test_broken_structure_is_domain_error(broken):
    with pytest.raises(MergeError):
        merge_decisions(broken, "A。", "B。", pending="revised")


def test_duplicate_ids_rejected():
    o, r = "A。B。C。D。", "A。X。C。Y。"
    rep = _decide(_audit(o, r))
    rep["changes"][1]["id"] = rep["changes"][0]["id"]
    with pytest.raises(MergeError, match="编号重复"):
        merge_decisions(rep, o, r)


def test_auto_passed_only_when_trained_and_not_needing_review():
    o, r, rep = _two()
    c = rep["changes"][0]
    c["decision"].update(status="auto_passed", final_text=c["revised"]["text"], decided_by="system",
                         decided_at=rep["run"]["created_at"])
    assert any("尚未训练" in e for e in decision_errors(c, trained=False))
    c["review"]["needs_review"] = True
    assert any("needs_review 为 false" in e for e in decision_errors(c, trained=True))
    c["review"]["needs_review"] = False
    assert decision_errors(c, trained=True, created_at=rep["run"]["created_at"]) == []


def test_raw_audit_and_reviewed_record_have_different_checks():
    o, r = "A。B。C。", "A。X。C。"
    raw = _audit(o, r)
    assert validate_report(raw, o, r) == [] and check_reviewed_report(raw, o, r) is None
    reviewed = _decide(raw)
    assert any("pending" in e for e in validate_report(reviewed, o, r))  # §9 只针对原始输出
    assert check_reviewed_report(reviewed, o, r) is None


# ---------------------------------------------------------------- 排版（空白）差异

def test_whitespace_only_is_not_identical():
    o, r = "甲句。乙句。\n丙句。\n", "甲句。 乙句。\n  丙句。"   # 段落数不变，只差空白
    rep = _audit(o, r)
    assert rep["changes"] == []
    diffs = layout_differences(rep, o, r)
    assert [d.kinds for d in diffs] == [["空格或制表符变化"], ["行首缩进变化"], ["增删换行"]]
    assert merge_decisions(rep, o, r).text == r                          # 默认：没有修改时用改稿排版
    assert merge_decisions(rep, o, r, layout="original").text == o
    assert merge_decisions(rep, o, r, layout="revised").text == r


def test_layout_policy_with_changes():
    o, r = "甲句。\n\n乙句可能成立。\n\n丙句。", "甲句。\n乙句成立。\n丙句。"
    rep = _audit(o, r)
    accepted = _decide(rep)
    follow = merge_decisions(accepted, o, r)
    assert follow.text == r and follow.layout_choices == ["revised"] * follow.layout_gaps
    keep_layout = merge_decisions(accepted, o, r, layout="original")
    assert "乙句成立。" in keep_layout.text and keep_layout.layout_choices == ["original"] * keep_layout.layout_gaps
    assert merge_decisions(_decide(rep, default="rejected"), o, r).text == o


def test_markdown_whitespace_flagged_as_structural():
    o = "# 题\n\n- 一级\n  - 二级\n\n```python\ndef f():\n    return 1\n```\n"
    r = "# 题\n\n- 一级\n- 二级\n\n```python\ndef f():\n  return 1\n```\n"
    rep = _audit(o, r)
    diffs = layout_differences(rep, o, r, markdown=True)
    risks = [d.markdown_risk for d in diffs]
    assert any(x and "列表层级" in x for x in risks)
    assert any(x and "代码块内" in x for x in risks)
    assert all(d.markdown_risk is None for d in layout_differences(rep, o, r))  # 纯文本不加 Markdown 说明
    res = merge_decisions(_decide(rep), o, r, markdown=True)
    md = review_record_markdown(_decide(rep), res, title="t", now=NOW)
    assert "## 排版（空白）差异" in md and "代码块内" in md and visible_whitespace("\n  ") in md


def test_document_segments_rebuild_revised():
    for name, o, r in PAIRS:
        rep = _audit(o, r)
        segs = document_segments(rep, o, r)
        dels = {c["id"] for c in rep["changes"] if c["alignment"] == "1-0"}
        rebuilt = "".join(s["gap"] + s["revised"] for s in segs if s["change_id"] not in dels)
        assert rebuilt == normalize(r), name
        assert sorted(s["change_id"] for s in segs if s["change_id"]) == sorted(c["id"] for c in rep["changes"])
