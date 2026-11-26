"""对齐与新增 / 删除定位（schema §2.4–§2.7，A3–A8、A10 及补充用例）。"""

import random

from acceptance_desk import audit, validate_report
from acceptance_desk.align import align
from acceptance_desk.text import normalize


def sides(p):
    return (p.alignment, (p.original.text, p.original.start, p.original.end, p.original.paragraph),
            (p.revised.text, p.revised.start, p.revised.end, p.revised.paragraph))


def assert_slices(o, r, pairs):
    for p in pairs:
        assert o[p.original.start:p.original.end] == p.original.text
        assert r[p.revised.start:p.revised.end] == p.revised.text


def test_a4_all_inserted():
    pairs = align("", "甲。乙。")
    assert [sides(p) for p in pairs] == [
        ("0-1", ("", 0, 0, None), ("甲。", 0, 2, 0)),
        ("0-1", ("", 0, 0, None), ("乙。", 2, 4, 0)),
    ]


def test_a5_all_deleted():
    pairs = align("甲。乙。", "")
    assert [sides(p) for p in pairs] == [
        ("1-0", ("甲。", 0, 2, 0), ("", 0, 0, None)),
        ("1-0", ("乙。", 2, 4, 0), ("", 0, 0, None)),
    ]


def test_a6_identical_and_a3_empty():
    t = "本研究采用问卷。\n\n结论成立。"
    assert align(t, t) == []
    assert align("", "") == []


def test_a8_split():
    o = "本研究采用问卷调查，并在三所高校收集数据。"
    r = "本研究采用问卷调查。数据在三所高校收集。"
    pairs = align(o, r)
    assert [p.alignment for p in pairs] == ["1-2"]
    assert (pairs[0].revised.start, pairs[0].revised.end) == (0, 20)
    assert pairs[0].revised.text == r


def test_merge_2_1():
    o = "本研究采用问卷调查。数据在三所高校收集。"
    r = "本研究采用问卷调查，并在三所高校收集数据。"
    pairs = align(o, r)
    assert [p.alignment for p in pairs] == ["2-1"]
    assert (pairs[0].original.start, pairs[0].original.end) == (0, 20)


def test_a10_duplicate_sentence_position():
    o, r = "同上。结论成立。同上。", "同上。结论成立。同上！"
    pairs = align(o, r)
    assert [sides(p) for p in pairs] == [("1-1", ("同上。", 8, 11, 0), ("同上！", 8, 11, 0))]


def test_insert_in_middle_and_delete_with_anchor():
    o = "第一句话在此。第二句话在此。第三句话在此。"
    r = "第一句话在此。新加入的一句。第三句话在此。"
    pairs = align(o, r)
    # “第二句话在此”与“新加入的一句”相似度低于阈值 → 一删一增；按原稿区间排序，零宽的 [7,7) 在前
    assert [sides(p) for p in pairs] == [
        ("0-1", ("", 7, 7, 0), ("新加入的一句。", 7, 14, 0)),
        ("1-0", ("第二句话在此。", 7, 14, 0), ("", 7, 7, 0)),
    ]


def test_extra_duplicates_with_insert_and_delete():
    """补充：重复句与新增、删除组合。删掉第一个“同上。”之后的句子，并在末尾新增一句。"""
    o = "同上。甲方提出了意见。同上。乙方表示同意。"
    r = "同上。同上。乙方表示同意。最后补充一句说明。"
    pairs = align(o, r)
    assert_slices(o, r, pairs)
    assert [sides(p) for p in pairs] == [
        ("1-0", ("甲方提出了意见。", 3, 11, 0), ("", 3, 3, 0)),
        ("0-1", ("", 21, 21, 0), ("最后补充一句说明。", 13, 22, 0)),
    ]


def test_extra_duplicate_paragraphs_insert_between():
    """补充：两个相同段落之间插入新段落，新增句定位到第一个相同段落之后。"""
    o = "重复的段落。\n\n重复的段落。"
    r = "重复的段落。\n\n新段落内容。\n\n重复的段落。"
    pairs = align(o, r)
    assert [sides(p) for p in pairs] == [("0-1", ("", 6, 6, 0), ("新段落内容。", 8, 14, 1))]


def test_extra_paragraph_deleted():
    o = "第一段。\n\n要删的整段，有两句。第二句。\n\n最后一段。"
    r = "第一段。\n\n最后一段。"
    pairs = align(o, r)
    assert [p.alignment for p in pairs] == ["1-0", "1-0"]
    assert all((p.revised.start, p.revised.end, p.revised.paragraph) == (4, 4, 0) for p in pairs)
    assert [p.original.paragraph for p in pairs] == [1, 1]


def test_whitespace_only_difference_is_reported():
    pairs = align("结论 成立。", "结论成立。")
    assert [p.alignment for p in pairs] == ["1-1"]


def test_order_and_ids_sorted():
    o = "甲句子一。乙句子二。"
    r = "新句子零。甲句子一。乙句子二改。"
    rep = audit(o, r, mode="rules_only")
    assert [c["id"] for c in rep["changes"]] == ["c001", "c002"]
    assert [c["alignment"] for c in rep["changes"]] == ["0-1", "1-1"]
    assert rep["changes"][0]["original"]["start"] == 0  # 前面没有已对齐句，取后一个的 start


def test_extra_random_edits_slice_exactly():
    """补充：随机组合 新增 / 删除 / 改写 / 重复句 / 换段，所有区间都能严格切片复原，报告自检通过。"""
    pool = ["同上。", "结论成立。", "问卷可能有效。", "样本来自三所高校[3]。", "误差为0.5%。",
            "（张三，2020）提出了这一观点。", "因此，本文建议增加讨论环节。", "实验共进行了三轮。"]
    rng = random.Random(20261108)
    for _ in range(300):
        o_sents = [rng.choice(pool) for _ in range(rng.randint(0, 7))]
        r_sents = []
        for s in o_sents:
            op = rng.random()
            if op < 0.15:
                continue  # 删除
            if op < 0.3:
                r_sents.append(s.replace("。", "！", 1))  # 改写
            else:
                r_sents.append(s)
            if rng.random() < 0.15:
                r_sents.append(rng.choice(pool))  # 新增（可能产生重复句）
        def join(ss):
            out = ""
            for s in ss:
                out += ("\n\n" if out and rng.random() < 0.2 else "") + s
            return out
        o, r = join(o_sents), join(r_sents)
        pairs = align(o, r)
        assert_slices(o, r, pairs)
        rep = audit(o, r, mode="rules_only")
        assert validate_report(rep, normalize(o), normalize(r)) == [], (o, r)


# ---- 回归：跨段落移动（PR #14 审阅 3）

def test_cross_paragraph_move_is_delete_plus_insert():
    o = "研究使用问卷。结果可能有效。\n\n本节讨论原因。"
    r = "研究使用问卷。\n\n结果可能有效。本节讨论原因。"
    pairs = align(o, r)
    assert_slices(o, r, pairs)
    assert [sides(p) for p in pairs] == [
        ("0-1", ("", 7, 7, 0), ("结果可能有效。", 9, 16, 1)),
        ("1-0", ("结果可能有效。", 7, 14, 0), ("", 7, 7, 0)),
    ]


def test_leading_paragraph_insert_does_not_shift_into_moves():
    """前面整段插入使段号整体 +1，且没有完全相同的段落可锚定；后续段落仍按内容配对。"""
    o = "第一段甲句子。第一段乙句子。\n\n第二段丙句子。第二段丁句子。"
    r = "新插入的段落内容。\n\n第一段甲句子。第一段乙句子改了。\n\n第二段丙句子。第二段丁句子改了。"
    pairs = align(o, r)
    assert_slices(o, r, pairs)
    assert [sides(p) for p in pairs] == [
        ("0-1", ("", 0, 0, 0), ("新插入的段落内容。", 0, 9, 0)),
        ("1-1", ("第一段乙句子。", 7, 14, 0), ("第一段乙句子改了。", 18, 27, 1)),
        ("1-1", ("第二段丁句子。", 23, 30, 1), ("第二段丁句子改了。", 36, 45, 2)),
    ]
