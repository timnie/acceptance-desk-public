"""工作台共用的决定状态与文字比对（review_ui/）。只用 rules_only，不加载模型。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 从当前源码目录加载被测模块

from acceptance_desk import audit, validate_report  # noqa: E402
from review_ui import render, state  # noqa: E402

ORIG = (ROOT / "examples" / "original.txt").read_text(encoding="utf-8")
REV = (ROOT / "examples" / "revised.txt").read_text(encoding="utf-8")
NOW = "2026-11-10T20:00:00+08:00"


@pytest.fixture(scope="module")
def report():
    return audit(ORIG, REV, mode="rules_only")


@pytest.fixture
def review(report):
    return state.new_review(report)


def _by_id(rv, cid):
    return state.find_change(rv, cid)


# ---------------------------------------------------------------- 状态操作

def test_initial_all_pending_and_report_untouched(report, review):
    assert report["summary"]["total_changes"] == 8 and report["summary"]["flagged"] == 7
    assert {c["decision"]["status"] for c in review["changes"]} == {"pending"}
    state.set_decision(review, "c001", "accepted", now=NOW)
    assert report["changes"][0]["decision"]["status"] == "pending"  # 深拷贝，不污染缓存
    assert validate_report(report, ORIG, REV) == []


def test_decision_fields_follow_schema(review):
    c = _by_id(review, "c001")
    d = state.set_decision(review, "c001", "accepted", now=NOW)
    assert d == {"status": "accepted", "final_text": c["revised"]["text"], "decided_by": "user",
                 "decided_at": NOW, "note": ""}
    state.set_decision(review, "c001", "rejected", now=NOW)
    assert c["decision"]["final_text"] == c["original"]["text"]
    state.set_note(review, "c001", "原文只说相关")
    state.set_decision(review, "c001", "edited", "问卷结果提示二者相关。", now=NOW)
    assert c["decision"]["final_text"] == "问卷结果提示二者相关。"
    state.set_decision(review, "c001", "pending")
    assert c["decision"] == {"status": "pending", "final_text": None, "decided_by": None,
                             "decided_at": None, "note": "原文只说相关"}


def test_deleted_sentence_accept_gives_empty_text(review):
    c = next(c for c in review["changes"] if c["alignment"] == "1-0")
    state.set_decision(review, c["id"], "accepted", now=NOW)
    assert c["decision"]["final_text"] == ""


def test_invalid_decisions_rejected(review):
    with pytest.raises(ValueError):
        state.set_decision(review, "c001", "auto_passed")
    with pytest.raises(ValueError):
        state.set_decision(review, "c001", "edited", None)
    with pytest.raises(KeyError):
        state.set_decision(review, "c999", "accepted")


def test_progress_counts(review):
    state.set_decision(review, "c001", "accepted", now=NOW)
    state.set_decision(review, "c006", "rejected", now=NOW)  # 中文数量候选
    p = state.progress(review)
    assert p["decided"] == 2 and p["total"] == 8
    assert p["flagged_pending"] == 5 and p["other_pending"] == 1
    assert p["by_status"] == {"accepted": 1, "rejected": 1, "pending": 6}


# ---------------------------------------------------------------- 保存 / 恢复

def _saved(review):
    state.set_decision(review, "c001", "edited", "问卷结果提示二者相关。", now=NOW)
    state.set_note(review, "c001", "保留限定")
    state.set_decision(review, "c004", "accepted", now=NOW)
    state.set_decision(review, "c008", "rejected", now=NOW)
    return state.export_review(review, now=NOW)


def test_roundtrip_restores_decisions(report, review):
    text = _saved(review)
    data = json.loads(text)
    assert data["review_meta"] == {"saved_at": NOW, "decided": 3, "total": 8,
                                   "by_status": {"edited": 1, "pending": 5, "accepted": 1, "rejected": 1}}
    restored = state.load_review(text.encode("utf-8"), report)
    assert [c["decision"] for c in restored["changes"]] == [c["decision"] for c in review["changes"]]
    assert "review_meta" not in restored
    # 除 decision 外与当前审计完全一致
    for a, b in zip(restored["changes"], report["changes"]):
        assert {k: v for k, v in a.items() if k != "decision"} == {k: v for k, v in b.items() if k != "decision"}


def test_reaudit_same_text_still_imports(review):
    text = _saved(review)
    again = audit(ORIG.replace("\n", "\r\n"), REV, mode="rules_only")  # 规范化后同一份
    assert state.load_review(text, again)["changes"][0]["decision"]["status"] == "edited"


def test_import_wrong_document(review):
    text = _saved(review)
    other = audit(ORIG, REV.replace("三轮", "四轮"), mode="rules_only")
    with pytest.raises(state.ReviewError, match="改稿与保存进度时的内容不一致"):
        state.load_review(text, other)


@pytest.mark.parametrize("bad, msg", [
    ("{not json", "进度文件无法读取"),
    ("[]", "没有可恢复的审阅记录"),
    (json.dumps({"document": {}, "changes": []}), "版本和当前验收台不兼容"),
])
def test_import_bad_json(report, bad, msg):
    with pytest.raises(state.ReviewError, match=msg):
        state.load_review(bad, report)


def test_import_non_utf8(report):
    with pytest.raises(state.ReviewError, match="不是 UTF-8"):
        state.load_review("审阅".encode("gbk"), report)


def _tamper(review, report, fn):
    data = json.loads(_saved(review))
    fn(data)
    with pytest.raises(state.ReviewError) as e:
        state.load_review(json.dumps(data, ensure_ascii=False), report)
    return str(e.value)


def test_import_rejects_changed_entries(review, report):
    assert "无法对应" in _tamper(review, report, lambda d: d["changes"].pop())
    assert "位置或文本" in _tamper(review, report, lambda d: d["changes"][1]["original"].update(start=0))
    assert "位置或文本" in _tamper(review, report, lambda d: d["changes"][1]["revised"].update(text="别的句子"))
    assert "编号或对齐" in _tamper(review, report, lambda d: d["changes"][2].update(id="c099"))


def test_import_rejects_bad_decisions(review, report):
    assert "最终文本与改句不一致" in _tamper(
        review, report, lambda d: d["changes"][3]["decision"].update(final_text="被偷改"))
    assert "尚未训练" in _tamper(review, report, lambda d: d["changes"][1]["decision"].update(status="auto_passed"))
    assert "缺少决定时间" in _tamper(review, report, lambda d: d["changes"][0]["decision"].update(decided_at=None))
    assert "待定条目" in _tamper(review, report, lambda d: d["changes"][1]["decision"].update(final_text="x"))
    assert "决定人" in _tamper(review, report, lambda d: d["changes"][0]["decision"].update(decided_by=None))


def test_failed_import_keeps_current_review(review, report):
    state.set_decision(review, "c002", "accepted", now=NOW)
    before = json.dumps(review, ensure_ascii=False)
    with pytest.raises(state.ReviewError):
        state.load_review("{", report)
    assert json.dumps(review, ensure_ascii=False) == before


# ---------------------------------------------------------------- 上传

def test_decode_upload():
    assert state.decode_upload("原稿\r\n".encode("utf-8")) == "原稿\r\n"
    assert state.decode_upload("﻿原稿".encode("utf-8")) == "原稿"  # BOM
    with pytest.raises(state.ReviewError, match="原稿.txt 不是 UTF-8"):
        state.decode_upload("原稿".encode("gbk"), "原稿.txt")


# ---------------------------------------------------------------- 渲染

def test_diff_html_marks_changes_and_evidence(report):
    o, r = render.diff_html(report["changes"][0])  # 删“可能”、加“直接”
    assert '<span class="ad-del ad-ev">可能</span>' in o
    assert 'class="ad-ins ad-ev">直接' in r
    assert "说明" in o and "ad-ins" not in o and "ad-del" not in r


def test_user_text_is_escaped():
    rep = audit("结论成立。", "结论<script>alert(1)</script>成立。", mode="rules_only")
    c = rep["changes"][0]
    _, r = render.diff_html(c)
    assert "<script>" not in r and "&lt;script&gt;" in r


# ---------------------------------------------------------------- 工作台导航（界面 v2）

def test_filters_and_step(review):
    order = [c["id"] for c in review["changes"]]
    flagged = state.visible_ids(review, "flagged")
    assert flagged == ["c001", "c002", "c003", "c005", "c006", "c007", "c008"]
    assert state.visible_ids(review, "other") == ["c004"]
    assert state.visible_ids(review, "all") == order
    assert state.step(order, flagged, "c003", +1) == "c005"
    assert state.step(order, flagged, "c003", -1) == "c002"
    assert state.step(order, flagged, "c001", -1) is None
    assert state.step(order, flagged, "c008", +1) is None
    # 当前条目已离开“待定”筛选：按原文顺序找最近的待定条目
    state.set_decision(review, "c003", "accepted", now=NOW)
    pending = state.visible_ids(review, "pending")
    assert "c003" not in pending
    assert state.step(order, pending, "c003", +1) == "c004"
    assert state.step(order, pending, "c003", -1) == "c002"
    assert state.step(order, [], "c003", +1) is None


def test_short_summary_and_pair(review):
    c1 = _by_id(review, "c001")
    assert render.short_summary(c1) == "删限定词 +2"
    assert render.short_summary(_by_id(review, "c004")) == "一句拆成两句"
    assert render.short_summary(_by_id(review, "c006")) == "新增数字"
    state.set_decision(review, "c001", "edited", "改<写>", now=NOW)
    out = render.pair_html(c1)
    assert "ad-final" in out and "改&lt;写&gt;" in out
    deleted = _by_id(review, "c008")
    assert "改稿删去了这一句" in render.pair_html(deleted)


# ---------------------------------------------------------------- .txt / .md 输入

MD_O = (ROOT / "examples" / "original.md").read_text(encoding="utf-8")
MD_R = (ROOT / "examples" / "revised.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name, fmt", [("a.txt", "txt"), ("章节.MD", "md"), ("x.markdown", "md"), ("b.TEXT", "txt")])
def test_format_of(name, fmt):
    assert state.format_of(name) == fmt


@pytest.mark.parametrize("name", ["paper.pdf", "paper.docx", "README", "a.md.bak"])
def test_format_rejects_other_files(name):
    with pytest.raises(state.ReviewError, match="不是 .txt 或 .md"):
        state.format_of(name)


def test_read_upload_md_keeps_source_exactly():
    data = "﻿".encode("utf-8") + MD_O.encode("utf-8")  # 带 BOM
    text, fmt = state.read_upload(data, "第三章.md", "原稿")
    assert fmt == "md" and text == MD_O  # 去掉 BOM，其余逐字不变
    for syntax in ("# 第三章", "## 3.1", "- 样本", "> 问卷", "[课程平台手册](https://", "```text", "[^1]:"):
        assert syntax in text
    with pytest.raises(state.ReviewError, match="改稿「第三章.md」 不是 UTF-8"):
        state.read_upload(MD_O.encode("gb18030"), "第三章.md", "改稿")


def test_md_audit_and_display_keep_syntax_escaped():
    rep = audit(MD_O, MD_R, mode="rules_only")
    texts = [(c["original"]["text"], c["revised"]["text"]) for c in rep["changes"]]
    assert ("## 3.1 样本", "### 3.1 样本") in texts  # 标题符号变化同样列为修改
    c3 = next(c for c in rep["changes"] if c["original"]["text"].startswith("> "))
    assert [r["code"] for r in c3["review"]["reasons"]] == ["hedge_removed", "certainty_added", "causal_upgrade"]
    link = next(c for c in rep["changes"] if "](https://" in c["original"]["text"])
    o, r = render.diff_html(link)
    assert "[课程平台" in o and "](https://example.edu/manual.html)" in r and "<a " not in r


def test_md_with_inline_html_is_escaped():
    o_txt = "结论成立。\n\n<details><summary>附注</summary>可能有效</details>\n"
    r_txt = "结论成立。\n\n<details><summary>附注</summary>有效 <img src=x onerror=alert(1)></details>\n"
    rep = audit(o_txt, r_txt, mode="rules_only")
    c = rep["changes"][0]
    o, r = render.diff_html(c)
    page = render.pair_html(c) + render.card_pair_html(c) + render.inline_diff_html(c)
    assert "<img" not in page and "<details>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in r.replace('<span class="ad-ins">', "").replace("</span>", "")


# ---------------------------------------------------------------- 导出（#18）

def test_export_names_never_reuse_uploaded_names():
    n = state.export_names("第三章", "md", draft=False)
    assert n == {"final": "第三章_终稿.md", "record": "第三章_审阅记录.md"}
    d = state.export_names("第三章", "txt", draft=True)
    assert d == {"final": "第三章_草稿.txt", "record": "第三章_审阅记录_草稿.md"}
    clash = state.export_names("第三章", "md", False, avoid=["第三章_终稿.MD", "第三章_终稿_导出.md"])
    assert clash["final"] == "第三章_终稿_导出_导出.md"
    assert state.export_names('a/b:c*"', "txt", False)["final"] == "a_b_c___终稿.txt"
    with pytest.raises(ValueError):
        state.export_names("x", "pdf", False)


def test_progress_round_trip_then_merge(report, review):
    from acceptance_desk import merge_decisions
    for c in review["changes"]:
        state.set_decision(review, c["id"], "accepted", now=NOW)
    state.set_decision(review, "c001", "edited", "问卷结果提示二者相关。", now=NOW)
    state.set_decision(review, "c008", "rejected", now=NOW)
    restored = state.load_review(state.export_review(review, now=NOW), report)
    a = merge_decisions(review, ORIG, REV)
    b = merge_decisions(restored, ORIG, REV)
    assert a.text == b.text and not a.draft
    assert "问卷结果提示二者相关。" in a.text and "本章最后说明数据来源。" in a.text


def test_progress_rejection_identifies_invalid_file_and_wrong_document_side():
    original, revised = "甲句。", "乙句。"
    current = audit(original, revised, mode="rules_only")
    saved = state.export_review(current).encode("utf-8")
    with pytest.raises(state.ReviewError, match="原始 .json"):
        state.parse_progress_file(b"{")
    with pytest.raises(state.ReviewError, match="下载进度文件"):
        state.parse_progress_file(b"{}")
    with pytest.raises(state.ReviewError, match="原稿和改稿放反了"):
        state.load_review(saved, audit(revised, original, mode="rules_only"))
    with pytest.raises(state.ReviewError, match="改稿与保存进度时的内容不一致"):
        state.load_review(saved, audit(original, revised + "\n", mode="rules_only"))
    assert state.load_review(saved, current) == current


def test_import_rejects_bool_offsets():
    """False == 0：位置用 true/false 冒充整数时也要拒绝。"""
    rep = audit("甲句。乙句。", "丙句。乙句。", mode="rules_only")
    assert rep["changes"][0]["original"]["start"] == 0
    rv = state.new_review(rep)
    data = json.loads(state.export_review(rv, now=NOW))
    data["changes"][0]["original"]["start"] = False
    with pytest.raises(state.ReviewError, match="位置或文本"):
        state.load_review(json.dumps(data, ensure_ascii=False), rep)


# ---------------------------------------------------------------- 检查依据：变化 / 规则 / 模型

def test_evidence_separates_fact_rule_and_model(report):
    c1 = json.loads(json.dumps(report["changes"][0]))   # 删“可能”、加“直接”、“相关”改为“导致”
    c1["review"]["reasons"].append({"source": "nli", "code": "nli_not_entailed", "detail": "x", "terms": []})
    ev = render.evidence(c1)
    assert "删去“可能”" in ev["observed"]
    assert any("直接" in t for t in ev["observed"])
    assert ev["rules"][0] == "删去限定词“可能”"
    assert ev["model"] == ["语义模型提示：从原句可能推不出改句的全部内容（模型判断，可能有误）"]
    typo = next(c for c in report["changes"] if c["id"] == "c006")
    assert render.evidence(typo) == {"observed": ["“论”改为“轮”"], "rules": ["新增数字“三轮”"], "model": []}
    ins = next(c for c in report["changes"] if c["alignment"] == "0-1")
    assert render.evidence(ins)["observed"] == ["改稿新增了这一句"]


def test_evidence_numbers_and_escaping():
    c = audit("有效问卷 172 份。", "有效问卷 182 份。", mode="rules_only")["changes"][0]
    assert "数字从 172 变为 182" in render.evidence(c)["observed"]
    c = audit("样本为 186 人。", "样本为 <b>196</b> 人。", mode="rules_only")["changes"][0]
    _, page = render.diff_html(c)
    assert "<b>" not in page and "&lt;b&gt;" in page


# ---------------------------------------------------------------- 撤销、批量与文稿视图（v3）

def test_undo_restores_full_decision(review):
    stack = []
    c = _by_id(review, "c001")
    state.set_note(review, "c001", "原备注")
    state.push_undo(stack, state.snapshot(review, ["c001"], label="编辑", cur="c001"))
    state.set_decision(review, "c001", "edited", "改写后的文本。", now=NOW)
    state.push_undo(stack, state.snapshot(review, ["c001"], label="备注", cur="c003"))
    state.set_note(review, "c001", "新备注")
    e = state.undo(review, stack)
    assert e["cur"] == "c003" and c["decision"]["note"] == "原备注" and c["decision"]["status"] == "edited"
    e = state.undo(review, stack)
    assert c["decision"] == {"status": "pending", "final_text": None, "decided_by": None, "decided_at": None,
                             "note": "原备注"}
    assert state.undo(review, stack) is None


def test_batch_only_touches_pending_in_scope_and_undoes_once(review):
    state.set_decision(review, "c002", "edited", "我的改写。", now=NOW)
    state.set_decision(review, "c003", "rejected", now=NOW)
    flagged = state.visible_ids(review, "flagged")
    targets = state.batch_targets(review, flagged)
    assert targets == ["c001", "c005", "c006", "c007", "c008"]           # 不含已编辑 / 已决定 / 范围外
    assert state.describe_ids(targets) == "第 1、5、6、7、8 处"
    stack = []
    state.push_undo(stack, state.snapshot(review, targets, label="批量", cur="c001"))
    done = state.batch_decide(review, targets, "accepted", now=NOW)
    assert done == targets
    assert _by_id(review, "c002")["decision"]["final_text"] == "我的改写。"
    assert _by_id(review, "c004")["decision"]["status"] == "pending"   # “其余修改”不在范围内
    state.undo(review, stack)
    assert [_by_id(review, t)["decision"]["status"] for t in targets] == ["pending"] * 5
    assert _by_id(review, "c003")["decision"]["status"] == "rejected"
    with pytest.raises(ValueError):
        state.batch_decide(review, targets, "edited")


def test_single_group_with_many_word_changes_is_one_decision(report):
    c1 = report["changes"][0]
    assert render.edit_count(c1) >= 3          # 删“可能”、加“直接”、“相关”→“导致了…”
    assert sum(1 for c in report["changes"] if c["id"] == "c001") == 1


def test_numbers_and_words_compared_as_units():
    c = audit("共回收有效问卷 412 份。", "共回收有效问卷 421 份。", mode="rules_only")["changes"][0]
    assert render.evidence(c)["observed"] == ["“412”改为“421”", "数字从 412 变为 421"]
    assert '<del class="ad-d" title="删去">412</del><ins class="ad-i" title="新增">421</ins>' in render.inline_diff_html(c)
    c = audit("采用 OLS 回归。", "采用 Logit 回归。", mode="rules_only")["changes"][0]
    assert render.evidence(c)["observed"] == ["“OLS”改为“Logit”"]


# ---------------------------------------------------------------- v4：长组折叠、全文收起前文、本机自动保存

def _long_change():
    filler = "受访者在访谈中反复提到，周末的开放时间、馆藏更新的速度以及座位是否充足，都会影响他们去图书馆的意愿"
    o = "从描述统计看，" + filler * 4 + "，这一差异可能说明可及性与阅读量相关<b>，" + filler * 3 + "。"
    r = "从描述统计看，" + filler * 4 + "，这一差异说明可及性提升了阅读量<b>，" + filler * 3 + "。"
    rep = audit(o, r, mode="rules_only")
    assert len(rep["changes"]) == 1
    return rep["changes"][0]


def test_long_group_card_is_clipped_but_full_text_is_kept():
    c = _long_change()
    assert render.is_long(c)
    card = render.card_pair_html(c)
    head, _, full = card.partition('<details class="ad-full">')
    assert full and "展开本组全文" in full
    assert head.count("ad-gap") >= 2 and "略" in head            # 两侧都有折叠
    assert '<span class="ad-del' in head and '<span class="ad-ins' in head  # 变化处仍然显示
    assert "可能" in head and "提升了" in head
    assert len(head) < len(render.pair_html(c)) * 0.7             # 卡片明显变短
    assert render.pair_html(c).split("</details>")[0] in card or render._pair(*render.diff_html(c)) in full
    assert "<b>" not in card and "&lt;b&gt;" in card              # 折叠后仍然转义


def test_short_group_card_is_not_clipped(report):
    c = report["changes"][0]
    assert not render.is_long(c)
    assert render.card_pair_html(c) == render.pair_html(c)


def test_keep_runs_covers_text_exactly_and_keeps_changes_visible():
    changed = [False] * 300
    changed[100:104] = [True] * 4
    changed[110] = True                                            # 两处变化之间的短间隔不折叠
    runs = render._keep_runs(changed)
    assert runs[0][0] == 0 and runs[-1][1] == 300
    assert all(a[1] == b[0] for a, b in zip(runs, runs[1:]))       # 首尾相接，不丢字、不重复
    shown = [(a, b) for a, b, keep in runs if keep]
    assert shown == [(100 - render.CLIP_CONTEXT, 111 + render.CLIP_CONTEXT)]
    assert render._keep_runs([False] * 300)[0] == (0, 2 * render.CLIP_CONTEXT, True)  # 无变化的一侧留开头
    assert render._keep_runs([]) == []


def test_edited_long_group_shows_final_text_outside_details():
    c = _long_change()
    c["decision"].update(status="edited", final_text="我的改写。", decided_by="user", decided_at=NOW)
    head = render.card_pair_html(c).partition('<details class="ad-full">')[0]
    assert "采用的文本（你的编辑）" in head and "我的改写。" in head


