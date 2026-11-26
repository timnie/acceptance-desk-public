"""段落 / 句子两级对齐（handoff §2.5，schema §2.4、§2.6、§2.7）。

1. 段落层：difflib.SequenceMatcher 锚定完全相同的段落；
   其余段落再按顺序配对（动态规划，总相似度最大），没配上的段落整段算新增 / 删除。
   句子只在配对的段落之间对齐，所以跨段落移动的句子输出为 1-0 加 0-1（schema §2.6）；
   前面整段插入导致段号平移时，后面的段落仍按内容配对，不会被误判为移动；
2. 句子层：在一对段落的句子上再用 SequenceMatcher 锚定完全相同的句子，
   剩下的非 equal 区段交给动态规划，在 1-1、1-2、2-1、0-1、1-0 中选总分最大的方案；
3. 新增 / 删除的空侧按前一个（没有则后一个）已对齐句子定位为零宽区间。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from .text import Sentence, split_paragraphs, split_sentences

# 一对句子允许配对的最低相似度。未调优的初始值，选定流程见 handoff §7
PAIR_MIN_RATIO = 0.5

# 动态规划的五种走法：(原稿句数, 改稿句数, 对齐类型)
_STEPS = ((1, 1, "1-1"), (1, 2, "1-2"), (2, 1, "2-1"), (1, 0, "1-0"), (0, 1, "0-1"))


@dataclass(frozen=True)
class Side:
    text: str
    start: int
    end: int
    paragraph: int | None


@dataclass(frozen=True)
class Pair:
    alignment: str  # "1-1" | "1-2" | "2-1" | "0-1" | "1-0"
    original: Side
    revised: Side


@dataclass
class _Item:
    """对齐序列里的一项。kind 为 "equal" 或五种对齐类型之一。"""
    kind: str
    orig: list[Sentence]
    rev: list[Sentence]


def pair_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _span_text(full: str, sents: list[Sentence]) -> str:
    return full[sents[0].start:sents[-1].end]


def _dp_align(o_full: str, r_full: str, orig: list[Sentence], rev: list[Sentence]) -> list[_Item]:
    """在一个非 equal 区段内选对齐方案：总分最大，同分时合并（1-2 / 2-1）次数少者优先。"""
    n, m = len(orig), len(rev)
    neg = (float("-inf"), 0)
    best = [[neg] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, str] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, 0)

    def score(di: int, dj: int, i: int, j: int) -> float | None:
        if di == 0 or dj == 0:
            return 0.0
        a = _span_text(o_full, orig[i - di:i])
        b = _span_text(r_full, rev[j - dj:j])
        sm = SequenceMatcher(None, a, b, autojunk=False)
        if sm.real_quick_ratio() < PAIR_MIN_RATIO or sm.quick_ratio() < PAIR_MIN_RATIO:
            return None  # 上界都不够，省去完整计算
        r = sm.ratio()
        return r if r >= PAIR_MIN_RATIO else None

    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            for di, dj, kind in _STEPS:
                if i < di or j < dj or best[i - di][j - dj] == neg:
                    continue
                s = score(di, dj, i, j)
                if s is None:
                    continue
                prev_score, prev_neg_merges = best[i - di][j - dj]
                merges = 1 if kind in ("1-2", "2-1") else 0
                cand = (round(prev_score + s, 9), prev_neg_merges - merges)
                if cand > best[i][j]:  # 严格大于：同分同合并数时保留 _STEPS 中靠前的走法
                    best[i][j] = cand
                    back[i][j] = (di, dj, kind)

    items: list[_Item] = []
    i, j = n, m
    while i or j:
        di, dj, kind = back[i][j]
        items.append(_Item(kind, orig[i - di:i], rev[j - dj:j]))
        i, j = i - di, j - dj
    items.reverse()
    return items


def _matching_regions(original: list[str], revised: list[str], anchor_repeats: bool
                      ) -> list[tuple[str, int, int, int, int]]:
    if anchor_repeats:
        return SequenceMatcher(None, original, revised, autojunk=False).get_opcodes()
    # 重复文本需要和邻近的改动一起按顺序配对，单凭相同文字无法固定是哪一份。
    repeated = {text for side in (original, revised) for text, count in Counter(side).items() if count > 1}
    left_marker, right_marker = object(), object()
    left = [left_marker if text in repeated else text for text in original]
    right = [right_marker if text in repeated else text for text in revised]
    return SequenceMatcher(None, left, right, autojunk=False).get_opcodes()


def _align_sentences(o_full: str, r_full: str, orig: list[Sentence], rev: list[Sentence],
                     anchor_repeats: bool) -> list[_Item]:
    items: list[_Item] = []
    regions = _matching_regions([s.text for s in orig], [s.text for s in rev], anchor_repeats)
    for tag, i1, i2, j1, j2 in regions:
        if tag == "equal":
            items.extend(_Item("equal", [orig[i1 + k]], [rev[j1 + k]]) for k in range(i2 - i1))
        else:
            items.extend(_dp_align(o_full, r_full, orig[i1:i2], rev[j1:j2]))
    return items


def _by_paragraph(sents: list[Sentence], count: int) -> list[list[Sentence]]:
    groups: list[list[Sentence]] = [[] for _ in range(count)]
    for s in sents:
        groups[s.paragraph].append(s)
    return groups


def _pair_paragraphs(o_texts: list[str], r_texts: list[str]) -> list[tuple[int | None, int | None]]:
    """非 equal 段落区段内按顺序配对段落：总相似度最大，同分时优先配对。
    返回 (原稿段下标 | None, 改稿段下标 | None) 序列；None 表示整段新增或删除。
    这里不设相似度下限：配对只限定句子在哪两段之间对齐，句子能否配对仍由 PAIR_MIN_RATIO 决定。"""
    n, m = len(o_texts), len(r_texts)
    best = [[float("-inf")] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            for di, dj in ((1, 1), (1, 0), (0, 1)):
                if i < di or j < dj:
                    continue
                gain = pair_ratio(o_texts[i - 1], r_texts[j - 1]) if di and dj else 0.0
                cand = round(best[i - di][j - dj] + gain, 9)
                if cand > best[i][j]:
                    best[i][j], back[i][j] = cand, (di, dj)
    out: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        di, dj = back[i][j]
        out.append((i - 1 if di else None, j - 1 if dj else None))
        i, j = i - di, j - dj
    out.reverse()
    return out


def _align_items(original: str, revised: str, *, anchor_repeats: bool = True) -> list[_Item]:
    o_paras, r_paras = split_paragraphs(original), split_paragraphs(revised)
    o_groups = _by_paragraph(split_sentences(original), len(o_paras))
    r_groups = _by_paragraph(split_sentences(revised), len(r_paras))
    o_texts = [original[s:e] for s, e in o_paras]
    r_texts = [revised[s:e] for s, e in r_paras]
    items: list[_Item] = []
    for tag, i1, i2, j1, j2 in _matching_regions(o_texts, r_texts, anchor_repeats):
        if tag == "equal":  # 段落文本相同 → 断句结果逐句相同
            for k in range(i2 - i1):
                items.extend(_Item("equal", [a], [b]) for a, b in zip(o_groups[i1 + k], r_groups[j1 + k]))
            continue
        for pi, pj in _pair_paragraphs(o_texts[i1:i2], r_texts[j1:j2]):
            if pi is None:
                items.extend(_Item("0-1", [], [s]) for s in r_groups[j1 + pj])
            elif pj is None:
                items.extend(_Item("1-0", [s], []) for s in o_groups[i1 + pi])
            else:
                items.extend(_align_sentences(original, revised, o_groups[i1 + pi], r_groups[j1 + pj], anchor_repeats))
    return items


def _side(full: str, sents: list[Sentence]) -> Side:
    return Side(_span_text(full, sents), sents[0].start, sents[-1].end, sents[0].paragraph)


def _anchor(items: list[_Item], k: int, side: str, has_text: bool) -> tuple[int, int | None]:
    """新增 / 删除的空侧定位（schema §2.4）：前一个已对齐项该侧的 end，否则后一个的 start，否则 0。"""
    for idx in range(k - 1, -1, -1):
        it = items[idx]
        if it.orig and it.rev:
            s = getattr(it, side)[-1]
            return s.end, s.paragraph
    for idx in range(k + 1, len(items)):
        it = items[idx]
        if it.orig and it.rev:
            s = getattr(it, side)[0]
            return s.start, s.paragraph
    return 0, (0 if has_text else None)


def align(original: str, revised: str, *, anchor_repeats: bool = True) -> list[Pair]:
    """对齐规范化全文，返回按 schema §2.7 排序的修改。

    anchor_repeats=False 时，重复段落和句子交给邻近位置一起配对。
    """
    items = _align_items(original, revised, anchor_repeats=anchor_repeats)
    o_has = bool(split_paragraphs(original))
    r_has = bool(split_paragraphs(revised))
    pairs: list[Pair] = []
    for k, it in enumerate(items):
        if it.kind == "equal":
            continue
        if it.kind == "0-1":
            pos, para = _anchor(items, k, "orig", o_has)
            pairs.append(Pair("0-1", Side("", pos, pos, para), _side(revised, it.rev)))
        elif it.kind == "1-0":
            pos, para = _anchor(items, k, "rev", r_has)
            pairs.append(Pair("1-0", _side(original, it.orig), Side("", pos, pos, para)))
        else:
            o, r = _side(original, it.orig), _side(revised, it.rev)
            if o.text == r.text:  # 对齐后完全相同的不进 changes
                continue
            pairs.append(Pair(it.kind, o, r))
    pairs.sort(key=lambda p: (p.original.start, p.original.end, p.revised.start, p.revised.end))
    return pairs
