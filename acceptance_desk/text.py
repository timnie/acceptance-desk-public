"""文本规范化、分段与断句（schema §2.1–§2.2）。

所有位置都是规范化后全文的 Python str 下标，半开区间 [start, end)。
句子位置在扫描过程中直接得到，不用 str.find / str.index 回查，所以重复句各有各的位置。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TERMINATORS = set("。！？!?")
# 紧跟句末标点、并入前一句的右引号和右括号（§2.2 第 2 条）
TRAILING_CLOSERS = set("”’」』）)】")
OPEN_BRACKETS = set("（(【[")
CLOSE_BRACKETS = set("）)】]")

_PARAGRAPH_SEP = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    paragraph: int
    text: str  # == full[start:end]


def normalize(text: str) -> str:
    """只把 \\r\\n 和 \\r 统一成 \\n，其余不动。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def split_paragraphs(text: str) -> list[tuple[int, int]]:
    """按 \\n\\s*\\n 分段，返回去掉首尾空白后的 (start, end)，纯空白段丢弃。"""
    spans = []
    pos = 0
    for m in _PARAGRAPH_SEP.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    out = []
    for s, e in spans:
        s, e = _strip_span(text, s, e)
        if s < e:
            out.append((s, e))
    return out


def _split_paragraph(text: str, start: int, end: int, paragraph: int) -> list[Sentence]:
    """在一个段落内断句。规则见 schema §2.2。"""
    raw: list[tuple[int, int]] = []
    seg_start = start
    depth = 0  # 未闭合括号层数
    i = start
    while i < end:
        ch = text[i]
        if ch == "\n":  # 第 7 条：段内换行断句；括号层数也在这里复位，避免一个未闭合括号吞掉全段
            raw.append((seg_start, i))
            seg_start = i + 1
            depth = 0
            i += 1
            continue
        if ch in OPEN_BRACKETS:
            depth += 1
        elif ch in CLOSE_BRACKETS:
            depth = max(0, depth - 1)
        elif ch in TERMINATORS and depth == 0:
            j = i + 1
            while j < end and text[j] in TERMINATORS:  # 第 1 条：连续句末标点合为一个边界
                j += 1
            while j < end and text[j] in TRAILING_CLOSERS:  # 第 2 条：右引号、右括号并入前一句
                j += 1
            raw.append((seg_start, j))
            seg_start = j
            i = j
            continue
        i += 1
    raw.append((seg_start, end))

    out = []
    for s, e in raw:
        s, e = _strip_span(text, s, e)
        if s < e:
            out.append(Sentence(s, e, paragraph, text[s:e]))
    return out


def split_sentences(text: str) -> list[Sentence]:
    """全文 → 句子列表，位置为全文偏移，paragraph 为段落序号（从 0 开始）。"""
    sentences: list[Sentence] = []
    for p, (s, e) in enumerate(split_paragraphs(text)):
        sentences.extend(_split_paragraph(text, s, e, p))
    return sentences
