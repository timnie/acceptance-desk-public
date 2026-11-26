"""存量提示按位置保留，不影响任何审计、决定或终稿。"""

import copy

import pytest

from acceptance_desk import ReviewDataError, audit, merge_decisions, original_hints


def hints(original, revised):
    report = audit(original, revised, mode="rules_only")
    before = copy.deepcopy(report)
    result = original_hints(report, original, revised)
    assert report == before
    for hit in result:
        normalized_o = original.replace("\r\n", "\n").replace("\r", "\n")
        normalized_r = revised.replace("\r\n", "\n").replace("\r", "\n")
        assert normalized_o[hit["start"]:hit["end"]] == hit["text"]
        assert normalized_r[hit["revised_start"]:hit["revised_end"]] == hit["text"]
    return result, report


def test_modified_and_untouched_sentences_and_no_risk_changes():
    original = "值得注意的是，到馆频率与阅读量存在相关。综上所述，本章的发现具有重要意义。"
    revised = "值得注意的是，到馆频率提升了阅读量。综上所述，本章的发现具有重要意义。"
    result, report = hints(original, revised)
    assert [(h["text"], h["change_id"]) for h in result] == [
        ("值得注意的是", "c001"), ("综上所述", None), ("具有重要意义", None)]
    assert result[0]["start"] == 0 and result[0]["end"] == 6
    assert report["summary"]["total_changes"] == 1
    assert {c["decision"]["status"] for c in report["changes"]} == {"pending"}
    assert merge_decisions(report, original, revised, pending="original").text == original
    assert merge_decisions(report, original, revised, pending="revised").text == revised


def test_repeated_phrase_uses_its_own_position():
    original = "值得注意的是，甲句成立。值得注意的是，乙句成立。"
    revised = "值得注意的是，甲句成立。乙句成立。"
    result, _ = hints(original, revised)
    assert len(result) == 1 and result[0]["start"] == 0
    assert result[0]["change_id"] is None


@pytest.mark.parametrize("original,revised", [
    ("值得注意的是，结论成立。", "值得关注的是，结论成立。"),
    ("值得注意的是，结论成立。", "值得特别注意的是，结论成立。"),
    ("随着技术的快速发展，结论成立。", "随着研究技术的快速发展，结论成立。"),
    ("当然，结论成立。", "结论成立。"),
    ("结论成立。", "总而言之，结论成立。"),
    ("结论可能在一定程度上成立。", "结论可能在一定程度上成立。"),
    ("", "总而言之，结论成立。"), ("当然。", ""), ("", ""),
])
def test_changed_added_deleted_or_out_of_scope_hits_are_not_stock(original, revised):
    result, _ = hints(original, revised)
    assert result == []


def test_unchanged_document_and_crlf_offsets():
    original = "第一段。\r\n\r\n当然，结论成立。"
    result, report = hints(original, original)
    assert len(result) == 1 and result[0]["start"] == len("第一段。\n\n")
    assert result[0]["sentence"]["paragraph"] == 1
    assert result[0]["category"] == "defensive"
    assert report["changes"] == [] and report["summary"]["needs_review"] == 0


def test_insertion_before_retained_phrase_maps_to_revised_offset():
    result, _ = hints("值得注意的是，结论成立。", "此外，值得注意的是，结论成立。")
    assert result[0]["start"] == 0 and result[0]["revised_start"] == 3


def test_stale_lexicon_and_changed_source_are_rejected():
    original = "当然，结论成立。"
    report = audit(original, original, mode="rules_only")
    report["run"]["lexicon_version"] = "older"
    with pytest.raises(ReviewDataError, match="词表版本"):
        original_hints(report, original, original)
    report["run"]["lexicon_version"] = "0.2"
    with pytest.raises(ReviewDataError, match="哈希"):
        original_hints(report, original + "字", original)


def test_omitted_change_is_not_misrepresented_as_stock():
    original, revised = "当然，结论成立。", "当然，结论尚未成立。"
    report = audit(original, revised, mode="rules_only")
    report["changes"] = []
    with pytest.raises(ReviewDataError, match="重新对齐"):
        original_hints(report, original, revised)
