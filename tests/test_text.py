"""断句与字符区间（schema §2.2–§2.3，A9）。"""

from acceptance_desk.text import normalize, split_paragraphs, split_sentences


def spans(text):
    return [(s.start, s.end) for s in split_sentences(text)]


def test_a9_sentence_boundaries():
    t = "准确率提高了3.5%（见表1。数据截至2025年）。他说：“结果显著。”随后的实验……仍在进行；结论待定！？最后一句"
    sents = split_sentences(t)
    assert [(s.start, s.end) for s in sents] == [(0, 26), (26, 36), (36, 54), (54, 58)]
    assert all(t[s.start:s.end] == s.text for s in sents)


def test_decimal_and_citations_not_split():
    t = "误差为0.05。研究[3]发现增长。（张三，2020）认为成立。版本v1.2.3有效。"
    assert [s.text for s in split_sentences(t)] == [
        "误差为0.05。", "研究[3]发现增长。", "（张三，2020）认为成立。", "版本v1.2.3有效。"]


def test_ellipsis_semicolon_newline_and_brackets():
    t = "甲……乙。丙；丁。\n戊（己。庚）辛！"
    assert [s.text for s in split_sentences(t)] == ["甲……乙。", "丙；丁。", "戊（己。庚）辛！"]


def test_unclosed_bracket_reset_at_newline():
    t = "甲（未闭合。乙。\n丙。丁。"
    assert [s.text for s in split_sentences(t)] == ["甲（未闭合。乙。", "丙。", "丁。"]


def test_paragraphs_and_whitespace():
    t = "  甲。乙。  \n \n\n丙。\n\n   \n\n"
    assert split_paragraphs(t) == [(2, 6), (12, 14)]
    sents = split_sentences(t)
    assert [(s.text, s.paragraph) for s in sents] == [("甲。", 0), ("乙。", 0), ("丙。", 1)]


def test_normalize_only_newlines():
    assert normalize("a\r\nb\rc　 ") == "a\nb\nc　 "


def test_duplicate_sentences_have_own_positions():
    t = "同上。结论成立。同上。"
    assert spans(t) == [(0, 3), (3, 8), (8, 11)]


def test_empty():
    assert split_sentences("") == []
    assert split_sentences(" \n\n \n") == []
