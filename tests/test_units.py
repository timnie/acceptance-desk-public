"""acceptance_desk.units.review_units：合句、拆句后的单侧记录归组。只用编写的例句。"""

from __future__ import annotations

import pytest

from acceptance_desk import ReviewDataError, audit, review_units


def _units(o: str, r: str, **kw):
    rep = audit(o, r, mode="rules_only")
    return rep, review_units(rep, o, r, **kw)


def test_three_sentences_merged_into_one_become_one_unit():
    o = ("开头一句保持不变。帕森斯把这个词译成铁笼。贝尔则强调外壳的意象。两种译名带来不同的读法。"
         "结尾一句保持不变。")
    r = "开头一句保持不变。帕森斯译成铁笼，贝尔强调外壳的意象，两种译名带来不同的读法。结尾一句保持不变。"
    rep, units = _units(o, r)
    assert len(rep["changes"]) > 1                                       # 对齐层面仍是配对加单侧记录
    assert any(not c["revised"]["text"] for c in rep["changes"])
    assert units == [{"id": "u001", "change_ids": [c["id"] for c in rep["changes"]], "one_sided": False}]


def test_unrelated_insertion_and_deletion_stay_separate():
    o = "第一句讲方法。第二句讲样本来源。"
    r = "第一句讲方法。总的来说意义重大。"
    rep, units = _units(o, r)
    assert [u["change_ids"] for u in units] == [[c["id"]] for c in rep["changes"]]
    assert all(u["one_sided"] for u in units) and len(units) == 2


def test_records_separated_by_unchanged_text_are_never_grouped():
    o = "甲方案提高了效率。中间这句不变。甲方案提高了效率并且更稳。"
    r = "甲方案显著提高了效率。中间这句不变。乙方案提高了效率并且更稳。"
    rep, units = _units(o, r)
    assert len(units) == len(rep["changes"]) == 2


def test_every_change_in_exactly_one_unit_and_order_follows_text():
    o = "\n\n".join(f"第{k}段的第一句话。第{k}段的第二句话。第{k}段的第三句话。" for k in range(1, 4))
    r = "\n\n".join(f"第{k}段的第一句话，第{k}段的第二句话。新增的一句。" for k in range(1, 4))
    rep, units = _units(o, r)
    ids = [cid for u in units for cid in u["change_ids"]]
    assert sorted(ids) == sorted(c["id"] for c in rep["changes"]) and len(ids) == len(set(ids))
    firsts = [u["change_ids"][0] for u in units]
    assert firsts == sorted(firsts)


def test_min_block_controls_how_little_shared_text_links_records():
    o = "前言保持不变。方法部分。研究对象为学生。"
    r = "前言保持不变。方法与对象另述。"
    rep = audit(o, r, mode="rules_only")
    loose = review_units(rep, o, r, min_block=2)
    strict = review_units(rep, o, r, min_block=8)
    assert len(loose) <= len(strict)
    with pytest.raises(ValueError):
        review_units(rep, o, r, min_block=0)
    with pytest.raises(ValueError):
        review_units(rep, o, r, min_block=True)


def test_report_must_match_texts():
    o, r = "原句可能成立。", "原句成立。"
    rep = audit(o, r, mode="rules_only")
    with pytest.raises(ReviewDataError):
        review_units(rep, o, r + "多了一句。")


def test_no_changes_no_units():
    rep, units = _units("一样。", "一样。")
    assert rep["changes"] == [] and units == []
