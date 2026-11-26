"""词表加载与匹配（schema §5.4，词表文件头部说明）。

匹配方式：七个类别加 ignore 的所有词条合在一起，从左到右扫描，
同一起点取最长命中，被命中的字符不再参与其他匹配；ignore 的命中直接丢弃。
含“…”的词条是模板，段间隔 0 到 max_gap 个字符，间隔里不含 gap_stop（不跨句）。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

BASE_CATEGORIES = ("qualifiers", "certainty", "correlation", "causal", "connective", "defensive", "slop")
CATEGORIES = (*BASE_CATEGORIES, "attribution")  # 0.2 起新增归因
# 0.2 以前的词表没有 attribution。旧报告按它记录的词表版本取类别，继续通过校验（Issue #51 方案 A）
_VERSIONS_WITHOUT_ATTRIBUTION = frozenset({"0.1", "0.1.1", "0.1.2"})


def categories_for(version: str) -> tuple[str, ...]:
    """某个词表版本有哪些类别。未登记的版本按当前类别处理。"""
    return BASE_CATEGORIES if version in _VERSIONS_WITHOUT_ATTRIBUTION else CATEGORIES
IGNORE = "ignore"
TEMPLATE_GAP = "…"


@dataclass(frozen=True)
class Lexicon:
    version: str
    max_gap: int
    gap_stop: str
    entries: tuple[tuple[str, str, re.Pattern], ...]  # (category, term, compiled)，含 ignore
    patterns: dict[str, re.Pattern] = field(hash=False)


def default_lexicon_path():
    return files("acceptance_desk").joinpath("resources/lexicon.toml")


def _compile_term(term: str, max_gap: int, gap_stop: str) -> re.Pattern:
    parts = term.split(TEMPLATE_GAP)
    if len(parts) == 1:
        return re.compile(re.escape(term))
    stop = "".join(re.escape(c) for c in gap_stop)
    # 贪婪间隔：同一起点取最长命中
    gap = f"[^{stop}]{{0,{max_gap}}}"
    return re.compile(gap.join(re.escape(p) for p in parts))


def load_lexicon(path: str | Path | None = None) -> Lexicon:
    if path is None:
        data = tomllib.loads(default_lexicon_path().read_text(encoding="utf-8"))
    else:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    matching = data["matching"]
    if matching.get("strategy", "leftmost_longest") != "leftmost_longest":
        raise ValueError(f"不支持的匹配策略：{matching['strategy']}")
    max_gap, gap_stop = int(matching["max_gap"]), matching["gap_stop"]
    entries = []
    version = str(data["version"])
    for cat in (*categories_for(version), IGNORE):
        for term in data[cat]["words"]:
            entries.append((cat, term, _compile_term(term, max_gap, gap_stop)))
    patterns = {k: re.compile(v) for k, v in data["patterns"].items()}
    return Lexicon(version, max_gap, gap_stop, tuple(entries), patterns)


def find_hits(text: str, lexicon: Lexicon, offset: int = 0) -> list[dict]:
    """最左最长、不重叠匹配。返回 {category, term, text, start, end}，位置为 offset + 局部下标。"""
    hits = []
    i, n = 0, len(text)
    while i < n:
        best = None  # (长度, category, term)；同长度保留词表中靠前的词条
        for cat, term, pat in lexicon.entries:
            m = pat.match(text, i)
            if m and m.end() > i and (best is None or m.end() - i > best[0]):
                best = (m.end() - i, cat, term)
        if best is None:
            i += 1
            continue
        length, cat, term = best
        if cat != IGNORE:
            hits.append({
                "category": cat, "term": term, "text": text[i:i + length],
                "start": offset + i, "end": offset + i + length,
            })
        i += length
    return hits


_NUMBER_TRANS = str.maketrans("０１２３４５６７８９％．−－﹣＋", "0123456789%.---+")


def _norm_number(s: str) -> str:
    """全角数字 / 百分号 / 小数点转半角，负号统一为 -，正号统一为 +（保留），删除内部空白。"""
    s = s.translate(_NUMBER_TRANS)
    return "".join(s.split())


def find_citations(text: str, lexicon: Lexicon) -> list[tuple[int, int, str]]:
    """两种引用正则，按位置排序、不重叠（方括号编号优先）。"""
    taken: list[tuple[int, int, str]] = []
    for key in ("citation_bracket", "citation_author_year"):
        for m in lexicon.patterns[key].finditer(text):
            if all(m.end() <= s or m.start() >= e for s, e, _ in taken):
                taken.append((m.start(), m.end(), m.group()))
    return sorted(taken)


def find_numbers(text: str, lexicon: Lexicon) -> list[str]:
    """在引用以外匹配数字，返回规范化后的数字（全角转半角、删内部空白）。"""
    masked = list(text)
    for s, e, _ in find_citations(text, lexicon):
        masked[s:e] = ["\x00"] * (e - s)
    out = []
    for m in lexicon.patterns["numbers"].finditer("".join(masked)):
        v = _norm_number(m.group())
        if v:
            out.append(v)
    return out
