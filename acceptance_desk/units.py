"""把相邻的修改记录归成审阅单元：解决“几句合成一句”被拆成配对加单侧新增、删除的问题。

句子对齐（align.py）只支持 1-1、1-2、2-1、0-1、1-0。改稿把三四句合写成一两句时，
多出来的原句会成为“删除整句”，多出来的改句会成为“新增整句”，读者得自己去找它们的内容
其实落在旁边哪一句里。这里不改对齐和 schema，只在报告之上加一层只读分组：

1. 按正文顺序，把两侧之间只隔空白的修改记录切成连续段；
2. 段内把各条记录的原稿侧、改稿侧分别拼起来，做一次字符级匹配；
3. 一条记录的原稿文字与另一条记录的改稿文字共享至少 min_block 个连续相同字符，两条就并为一组。

同一组的记录可以不相邻（原稿和改稿的句序交错时就会这样），组按首条记录的位置排序。
分组只影响怎么一起看，不改任何修改记录、决定或候选原因；每条记录恰好属于一个单元。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from ._derived import checked_source
from ._schema import numbered_id

MIN_BLOCK = 3  # 共享片段至少这么多字才算同一处内容；2 字容易碰上“研究”“的是”这类偶合


def _contiguous_runs(changes: list[dict], original: str, revised: str) -> list[list[int]]:
    runs: list[list[int]] = []
    for k, c in enumerate(changes):
        if runs:
            prev = changes[runs[-1][-1]]
            gap_o = original[prev["original"]["end"]:c["original"]["start"]]
            gap_r = revised[prev["revised"]["end"]:c["revised"]["start"]]
            if not gap_o.strip() and not gap_r.strip():
                runs[-1].append(k)
                continue
        runs.append([k])
    return runs


def _link(run: list[int], changes: list[dict], min_block: int) -> list[list[int]]:
    """段内按共享文字做并查集，返回各组（组内按原顺序）。"""
    parent = {k: k for k in run}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def concat(side: str) -> tuple[str, list[int]]:
        parts, owner = [], []
        for k in run:
            t = changes[k][side]["text"]
            parts.append(t)
            owner += [k] * len(t)
        return "".join(parts), owner

    o_text, o_owner = concat("original")
    r_text, r_owner = concat("revised")
    for block in SequenceMatcher(None, o_text, r_text, autojunk=False).get_matching_blocks():
        if block.size < min_block:
            continue
        for t in range(block.size):
            a, b = find(o_owner[block.a + t]), find(r_owner[block.b + t])
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for k in run:
        groups.setdefault(find(k), []).append(k)
    return sorted(groups.values())


def review_units(report: dict, original: str, revised: str, *, min_block: int = MIN_BLOCK) -> list[dict]:
    """返回审阅单元列表：[{"id": "u001", "change_ids": [...], "one_sided": bool}]。

    one_sided 为真表示这一组仍然只有一侧有文字（真正新增或删除的整句、过渡语）。
    报告必须与两份全文对应（哈希、位置都会核对），否则抛 ReviewDataError。
    """
    if type(min_block) is not int or min_block < 1:
        raise ValueError("min_block 必须是正整数")
    original, revised, _ = checked_source(report, original, revised)
    changes = report["changes"]
    groups = [g for run in _contiguous_runs(changes, original, revised) for g in _link(run, changes, min_block)]
    groups.sort(key=lambda g: g[0])
    units = []
    for n, g in enumerate(groups, 1):
        has_o = any(changes[k]["original"]["text"] for k in g)
        has_r = any(changes[k]["revised"]["text"] for k in g)
        units.append({"id": numbered_id("u", n, len(groups)), "change_ids": [changes[k]["id"] for k in g],
                      "one_sided": not (has_o and has_r)})
    return units
