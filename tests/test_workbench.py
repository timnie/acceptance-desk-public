"""文稿工作台里不依赖界面框架的部分：显示整理（workbench/view.py）。存量提示和修改意见的核心在 acceptance_desk，另有测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 从当前源码目录加载被测模块

from acceptance_desk import audit, document_segments, feedback_markdown, original_hints  # noqa: E402
from review_ui import state  # noqa: E402
from workbench import view  # noqa: E402

ORIG = (ROOT / "examples" / "long_original.txt").read_text(encoding="utf-8")
REV = (ROOT / "examples" / "long_revised.txt").read_text(encoding="utf-8")
NOW = "2026-11-12T10:00:00+08:00"


@pytest.fixture
def review():
    return state.new_review(audit(ORIG, REV, mode="rules_only"))


def test_paragraphs_keep_every_segment_in_order(review):
    segs = document_segments(review, ORIG, REV)
    paras = view.paragraphs(segs)
    assert len(paras) == 6 and [s for p in paras for s in p] == segs
    assert sum(s["change_id"] is not None for s in segs) == len(review["changes"]) == 13


def test_group_html_shows_trace_when_pending_or_current(review):
    c = state.find_change(review, "c005")
    assert '<del class="ad-d" title="删去">可能</del>' in view.group_html(c, False)
    state.set_decision(review, "c005", "rejected", now=NOW)
    done = view.group_html(c, False)
    assert done.startswith('<sup class="wb-m">5 留</sup>') and "<del" not in done and "可能说明" in done
    assert "<del" in view.group_html(c, True)                      # 回看时仍能看到增删痕迹


def test_empty_result_is_worded_by_what_happened(review):
    inserted = next(c for c in review["changes"] if c["alignment"] == "0-1")
    deleted = next(c for c in review["changes"] if c["alignment"] == "1-0")
    state.set_decision(review, inserted["id"], "rejected", now=NOW)   # 不采用新增句：原稿里本来没有，不能说“已删去”
    state.set_decision(review, deleted["id"], "accepted", now=NOW)
    assert "没有采用新增的这一句" in view.group_html(inserted, False)
    assert "这一句已删去" in view.group_html(deleted, False)


def test_nature_and_purpose_come_only_from_rule_evidence(review):
    stronger = state.find_change(review, "c005")
    assert view.nature(stronger) == [("论证强度提升", "warn")]
    purpose, basis = view.likely_purpose(stronger)
    assert purpose == "让结论读起来更肯定" and "可能" in basis      # 推断必须带着依据
    plain = state.find_change(review, "c001")                        # 没有规则命中：不给性质，也不猜目的
    assert view.nature(plain) == [] and view.likely_purpose(plain) is None


def test_sentence_insert_and_delete_describe_changes_without_guessing_intent():
    original = "样本规模有限。结论仍有待验证。"
    revised = "样本规模有限。由此可见，结论成立。"
    changes = audit(original, revised, mode="rules_only")["changes"]
    inserted = next(change for change in changes if change["alignment"] == "0-1")
    deleted = next(change for change in changes if change["alignment"] == "1-0")
    effect, basis = view.likely_purpose(inserted)
    assert "由此可见" in effect and "前文是否支持" in effect
    assert "由此可见" in basis
    assert view.likely_purpose(deleted) is None
    assert "（本句已删除）" in view.comparison_html(deleted, "revised")


def test_hints_are_placed_on_segments_and_marked_in_untouched_text():
    o = "值得注意的是，样本量较小。当然，结论可能成立。总而言之，仍需验证。"
    r = "值得注意的是，样本量较小。当然，结论成立。总而言之，仍需验证。"
    rep = audit(o, r, mode="rules_only")
    segs = document_segments(rep, o, r)
    hints = original_hints(rep, o, r)
    placed = view.hints_by_segment(segs, o, hints)
    assert sum(len(v) for v in placed.values()) == len(hints) >= 3           # 一条不丢
    untouched = [h for hs in placed.values() for h in hs if h["local_start"] is not None]
    inside = [h for hs in placed.values() for h in hs if h["local_start"] is None]
    assert {h["text"] for h in untouched} >= {"值得注意的是", "总而言之"}
    assert [h["text"] for h in inside] == ["当然"] and inside[0]["change_id"] == "c001"   # 被修改的句子里原样保留的词
    for i, hs in placed.items():
        for h in hs:
            if h["local_start"] is not None:                                   # 句内位置指向的确实是这个词
                assert segs[i]["original"][h["local_start"]:h["local_end"]] == h["text"]
    i = next(i for i, hs in placed.items() if hs[0]["text"] == "总而言之")
    marked = view.plain_html("  " + segs[i]["original"], placed[i], shift=2)
    assert '<span class="wb-old" title="原稿已有的套话，本轮没有改动">总而言之</span>' in marked
    assert rep["summary"]["total_changes"] == 1                              # 存量提示不改变修改的计数


def test_segment_starts_follow_document_order_with_repeated_sentences():
    o = "同一句话。同一句话。结论可能成立。"
    r = "同一句话。同一句话。结论成立。"
    rep = audit(o, r, mode="rules_only")
    segs = document_segments(rep, o, r)
    starts = view.segment_starts(segs, o)
    found = [(a, s) for a, s in zip(starts, segs) if s["original"]]            # 文末可能有一个空片段，位置为 None
    assert all(o[a:a + len(s["original"])] == s["original"] for a, s in found)
    assert [a for a, _ in found] == [0, 5, 10]                                   # 两句相同的话各占各的位置


def test_plain_html_escapes_text_around_hints():
    text = "<b>值得注意的是</b>，x<y。"
    html_out = view.plain_html(text, [{"category": "slop", "local_start": 3, "local_end": 9, "text": "值得注意的是"}])
    assert "<b>" not in html_out and "&lt;b&gt;" in html_out and "x&lt;y" in html_out


def test_core_feedback_export_from_workbench_state(review):
    """工作台用核心的 feedback_markdown 导出；这里只确认工作台的状态能直接交给它。"""
    state.set_decision(review, "c005", "rejected", now=NOW)
    state.set_note(review, "c005", "问卷只能说明相关")
    state.set_decision(review, "c006", "edited", "也有人觉得馆藏更新慢。", now=NOW)
    state.set_decision(review, "c002", "accepted", now=NOW)
    state.set_note(review, "c002", "数字我核对过")
    text = feedback_markdown(review, ORIG, REV, title="示例", scope="local", now=NOW)
    rewrite = feedback_markdown(review, ORIG, REV, title="示例", scope="rewrite", now=NOW)
    assert "问卷只能说明相关" in text and "也有人觉得馆藏更新慢。" in text
    assert text.startswith("# 作者修改意见\n\n## 修订范围 · 局部修订")
    assert rewrite.startswith("# 作者修改意见\n\n## 修订范围 · 整体改写")
    assert "其余文字尽量保持不变" in text and "重新改写结构和表达" in rewrite
    assert "请保留作者已采用" in text and "供整体改写时参考" in rewrite
    assert "不要重写" not in rewrite and "规则命中" not in text and "用词检查提示" in text
