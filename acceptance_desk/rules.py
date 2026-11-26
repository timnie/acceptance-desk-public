"""规则特征（schema §5.3、§5.4）。

对一处修改的两侧分别做词表匹配，按次数相减得到 delta，再展开成“删去 / 新增”列表；
另外比较数字、引用的多重集，计算长度比和字符级编辑距离比。
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

from .align import Side
from .lexicon import Lexicon, categories_for, find_citations, find_hits, find_numbers


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _changed_regions(a: str, b: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """非 equal opcodes 在两侧覆盖的字符区间（局部下标）。"""
    ra, rb = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            if i2 > i1:
                ra.append((i1, i2))
            if j2 > j1:
                rb.append((j1, j2))
    return ra, rb


def _overlaps(s: int, e: int, regions: list[tuple[int, int]]) -> bool:
    return any(s < r2 and r1 < e for r1, r2 in regions)


def _expand(delta: dict[str, int], order: list[str], sign: int) -> list[str]:
    """把 delta 中符号为 sign 的项按次数展开，词条顺序按 order（该侧出现顺序）中的第一次出现。"""
    out, seen = [], set()
    for term in order:
        if term in seen:
            continue
        seen.add(term)
        n = delta.get(term, 0) * sign
        out.extend([term] * max(n, 0))
    return out


def _multiset_diff(orig: list[str], rev: list[str]) -> tuple[list[str], list[str]]:
    d = dict(Counter(rev))
    for v in orig:
        d[v] = d.get(v, 0) - 1
    return _expand(d, rev, +1), _expand(d, orig, -1)


def rule_features(orig: Side, rev: Side, lexicon: Lexicon) -> dict:
    o_hits = find_hits(orig.text, lexicon, offset=orig.start)
    r_hits = find_hits(rev.text, lexicon, offset=rev.start)

    both = bool(orig.text) and bool(rev.text)
    if both:
        o_reg, r_reg = _changed_regions(orig.text, rev.text)
    hits = []
    for side_name, side, side_hits, regions in (
        ("original", orig, o_hits, o_reg if both else None),
        ("revised", rev, r_hits, r_reg if both else None),
    ):
        for h in side_hits:
            changed = True if regions is None else _overlaps(h["start"] - side.start, h["end"] - side.start, regions)
            hits.append({"side": side_name, **h, "in_changed_region": changed})

    delta: dict[str, dict[str, int]] = {}
    cats = categories_for(lexicon.version)
    order_o: dict[str, list[str]] = {c: [] for c in cats}
    order_r: dict[str, list[str]] = {c: [] for c in cats}
    for cat in cats:
        co = Counter(h["term"] for h in o_hits if h["category"] == cat)
        cr = Counter(h["term"] for h in r_hits if h["category"] == cat)
        order_o[cat] = [h["term"] for h in o_hits if h["category"] == cat]
        order_r[cat] = [h["term"] for h in r_hits if h["category"] == cat]
        d = {}
        for term in dict.fromkeys(order_o[cat] + order_r[cat]):
            v = cr[term] - co[term]
            if v:
                d[term] = v
        delta[cat] = d

    def removed(cat: str) -> list[str]:
        return _expand(delta[cat], order_o[cat], -1)

    def added(cat: str) -> list[str]:
        return _expand(delta[cat], order_r[cat], +1)

    nums_added, nums_removed = _multiset_diff(find_numbers(orig.text, lexicon), find_numbers(rev.text, lexicon))
    cits_added, cits_removed = _multiset_diff(
        [c for _, _, c in find_citations(orig.text, lexicon)],
        [c for _, _, c in find_citations(rev.text, lexicon)],
    )

    lo, lr = len(orig.text), len(rev.text)
    return {
        "hits": hits,
        "delta": delta,
        "hedge_removed": removed("qualifiers"),
        "hedge_added": added("qualifiers"),
        "certainty_added": added("certainty"),
        "certainty_removed": removed("certainty"),
        "causal_upgrade": both and sum(delta["correlation"].values()) < 0 and sum(delta["causal"].values()) > 0,
        "connective_added": added("connective"),
        "connective_removed": removed("connective"),
        "defensive_added": added("defensive"),
        "slop_added": added("slop"),
        **({"attribution_added": added("attribution")} if "attribution" in cats else {}),
        "numbers_added": nums_added,
        "numbers_removed": nums_removed,
        "citations_added": cits_added,
        "citations_removed": cits_removed,
        "length_original": lo,
        "length_revised": lr,
        "length_ratio": round(lr / lo, 3) if both else None,
        "char_edit_ratio": round(levenshtein(orig.text, rev.text) / lo, 3) if both else None,
    }
