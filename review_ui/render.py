"""把一处修改渲染成给人看的 HTML 片段。所有用户文本先 html.escape 再拼标签。"""

from __future__ import annotations

import html

from acceptance_desk.evidence import NLI_MESSAGES, paired_text_changes, text_opcodes

ALIGNMENT_ZH = {"1-1": "", "1-2": "一句拆成两句", "2-1": "两句合为一句", "0-1": "新增整句", "1-0": "删除整句"}
# 导航里的简短摘要（每处最多两个）
_SHORT = {
    "sentence_inserted": "新增句", "sentence_deleted": "删除句",
    "hedge_removed": "删限定词", "hedge_added": "加限定词", "certainty_added": "加确定性词",
    "connective_added": "加推论连接", "defensive_added": "加防御表述", "slop_added": "套话",
    "number_added": "新增数字", "number_removed": "删去数字",
    "citation_added": "引用变动", "citation_removed": "引用变动",
    "causal_upgrade": "因果升级", "nli_not_entailed": "语义变化", "nli_contradiction": "可能矛盾",
}


def char_opcodes(o: str, r: str) -> list[tuple[str, int, int, int, int]]:
    """按核心证据使用的比对单位返回字符区间。"""
    return text_opcodes(o, r)


def observed_changes(change: dict, limit: int = 4) -> list[str]:
    """观察到的文字变化（由字符比对直接得到的事实），例如：删去“可能”；“相关”改为“导致了”。"""
    al = change["alignment"]
    if al == "0-1":
        return ["改稿新增了这一句"]
    if al == "1-0":
        return ["改稿删去了这一句"]
    o, r = change["original"]["text"], change["revised"]["text"]
    out = paired_text_changes(al, o, r, limit)
    rules = change["features"]["rules"]
    na, nr = rules.get("numbers_added") or [], rules.get("numbers_removed") or []
    if na and nr:
        out.append(f"数字从 {'、'.join(nr)} 变为 {'、'.join(na)}")
    return out or ["文本有变化"]


def evidence(change: dict) -> dict[str, list[str]]:
    """检查依据，分三类：observed 观察到的变化（事实）、rules 规则命中、model 模型提示（判断）。"""
    rules = [r["detail"] for r in change["review"]["reasons"] if r["source"] == "rule"]
    model = [NLI_MESSAGES.get(r["code"], r["detail"]) for r in change["review"]["reasons"] if r["source"] == "nli"]
    return {"observed": observed_changes(change), "rules": rules, "model": model}


def edit_count(change: dict) -> int:
    """本组里的字词变化处数（只用于说明“本组包含几处变化”，决定仍按整组）。"""
    o, r = change["original"]["text"], change["revised"]["text"]
    return sum(op[0] != "equal" for op in char_opcodes(o, r))


def short_summary(change: dict) -> str:
    """导航用的简短摘要：首个原因的短标签，其余计数，例如“删限定词 +2”。"""
    tags = list(dict.fromkeys(_SHORT.get(r["code"], "") for r in change["review"]["reasons"]))
    tags = [t for t in tags if t]
    if not tags:
        return ALIGNMENT_ZH.get(change["alignment"]) or "字词调整"
    return tags[0] + (f" +{len(tags) - 1}" if len(tags) > 1 else "")


def _evidence_spans(change: dict, side: str) -> list[tuple[int, int]]:
    """该侧应加粗的证据词（句内相对位置）：位于改动区域的词表命中，或出现在原因里的词条。"""
    s = change[side]
    reason_terms = {t for r in change["review"]["reasons"] for t in r["terms"]}
    spans = []
    for h in change["features"]["rules"]["hits"]:
        if h["side"] != side:
            continue
        if h["in_changed_region"] or h["term"] in reason_terms:
            a, b = h["start"] - s["start"], h["end"] - s["start"]
            if 0 <= a <= b <= len(s["text"]):
                spans.append((a, b))
    return spans


def _markup(text: str, changed: list[bool], evidence: list[bool], changed_cls: str, empty: str) -> str:
    if not text:
        return f'<span class="ad-empty">{empty}</span>'
    parts, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and changed[j] == changed[i] and evidence[j] == evidence[i]:
            j += 1
        cls = " ".join(c for c, on in ((changed_cls, changed[i]), ("ad-ev", evidence[i])) if on)
        chunk = html.escape(text[i:j])
        parts.append(f'<span class="{cls}">{chunk}</span>' if cls else chunk)
        i = j
    return "".join(parts)


def _diff_sides(change: dict) -> list[tuple[str, list[bool], list[bool], str, str]]:
    """两侧各一组 (文本, 逐字是否改动, 逐字是否证据词, 改动样式, 空侧说明)。"""
    o, r = change["original"]["text"], change["revised"]["text"]
    o_chg, r_chg = [False] * len(o), [False] * len(r)
    for tag, i1, i2, j1, j2 in char_opcodes(o, r):
        if tag != "equal":
            o_chg[i1:i2] = [True] * (i2 - i1)
            r_chg[j1:j2] = [True] * (j2 - j1)
    sides = []
    for side, text, chg, cls, empty in (("original", o, o_chg, "ad-del", "（原稿没有这一句）"),
                                         ("revised", r, r_chg, "ad-ins", "（改稿删去了这一句）")):
        ev = [False] * len(text)
        for a, b in _evidence_spans(change, side):
            ev[a:b] = [True] * (b - a)
        sides.append((text, chg, ev, cls, empty))
    return sides


def diff_html(change: dict) -> tuple[str, str]:
    """返回 (原句 HTML, 改句 HTML)：改动的字加浅底色，证据词加粗点线。"""
    o, r = (_markup(*side) for side in _diff_sides(change))
    return o, r


def _pair(o: str, r: str) -> str:
    return (f'<div class="ad-pair"><div><div class="ad-label">原句</div><div class="ad-sent">{o}</div></div>'
            f'<div><div class="ad-label">改句</div><div class="ad-sent">{r}</div></div></div>')


def _final_html(change: dict) -> str:
    dec = change["decision"]
    if dec["status"] != "edited":
        return ""
    text = html.escape(dec["final_text"]) or '<span class="ad-empty">（空：删去这一句）</span>'
    return f'<div class="ad-label">采用的文本（你的编辑）</div><div class="ad-sent ad-final">{text}</div>'


def pair_html(change: dict) -> str:
    """原句、改句并排（窄屏上下排列）；若已自行编辑，再显示采用的文本。"""
    return _pair(*diff_html(change)) + _final_html(change)


# ---------------------------------------------------------------- 长组折叠

CLIP_MIN = 160      # 原句或改句超过这么多字，卡片才折叠
CLIP_CONTEXT = 20   # 每处变化前后各保留的字数
CLIP_MIN_GAP = 12   # 短于这么多字的未改动片段不值得折叠
CLIP_MIN_SAVED = 40  # 折叠省下的字数少于此值就不折叠


def _keep_runs(changed: list[bool], context: int = CLIP_CONTEXT) -> list[tuple[int, int, bool]]:
    """把一侧文本切成连续区间 (起, 止, 是否显示)：变化处及前后 context 字显示，其余折叠。

    该侧没有任何变化（例如变化全在另一侧）时，保留开头 2*context 字，让人知道是哪一句。
    """
    n = len(changed)
    if n == 0:
        return []
    keep = [False] * n
    idx = [i for i, c in enumerate(changed) if c]
    if not idx:
        keep[:2 * context] = [True] * min(n, 2 * context)
    for i in idx:
        lo, hi = max(0, i - context), min(n, i + context + 1)
        keep[lo:hi] = [True] * (hi - lo)
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and keep[j] == keep[i]:
            j += 1
        runs.append([i, j, keep[i]])
        i = j
    for run in runs:                      # 太短的折叠片段直接显示
        if not run[2] and run[1] - run[0] < CLIP_MIN_GAP:
            run[2] = True
    merged: list[list] = []
    for run in runs:
        if merged and merged[-1][2] == run[2]:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return [(a, b, k) for a, b, k in merged]


def _clipped_side(text: str, changed: list[bool], ev: list[bool], cls: str, empty: str) -> tuple[str, int]:
    """返回 (折叠后的 HTML, 折叠掉的字数)。"""
    if not text:
        return _markup(text, changed, ev, cls, empty), 0
    parts, hidden = [], 0
    for a, b, keep in _keep_runs(changed):
        if keep:
            parts.append(_markup(text[a:b], changed[a:b], ev[a:b], cls, empty))
        else:
            hidden += b - a
            parts.append(f'<span class="ad-gap" title="未改动的文字，已折叠">…略 {b - a} 字…</span>')
    return "".join(parts), hidden


def is_long(change: dict) -> bool:
    return max(len(change["original"]["text"]), len(change["revised"]["text"])) > CLIP_MIN


def card_pair_html(change: dict) -> str:
    """卡片用的原句 / 改句：长组只显示变化处及其前后文字，全文放进可展开的 <details>（原生 HTML，不用脚本）。

    折叠只影响卡片的显示；决定、合并、导出仍按整组全文。短组或折叠省不了多少字时，与 pair_html 相同。
    """
    if not is_long(change):
        return pair_html(change)
    (o, o_hidden), (r, r_hidden) = (_clipped_side(*side) for side in _diff_sides(change))
    if o_hidden + r_hidden < CLIP_MIN_SAVED:
        return pair_html(change)
    n_o, n_r = len(change["original"]["text"]), len(change["revised"]["text"])
    return (_pair(o, r) + _final_html(change)
            + f'<details class="ad-full"><summary>展开本组全文（原句 {n_o} 字，改句 {n_r} 字）</summary>'
            + _pair(*diff_html(change)) + "</details>")


# ---------------------------------------------------------------- 文稿视图（v3）

def inline_diff_html(change: dict) -> str:
    """一组修改的行内差异：删除 <del>（删除线）、新增 <ins>（下划线），不只靠颜色区分。"""
    o, r = change["original"]["text"], change["revised"]["text"]
    parts = []
    for tag, i1, i2, j1, j2 in char_opcodes(o, r):
        if tag == "equal":
            parts.append(html.escape(o[i1:i2]))
            continue
        if i2 > i1:
            parts.append(f'<del class="ad-d" title="删去">{html.escape(o[i1:i2])}</del>')
        if j2 > j1:
            parts.append(f'<ins class="ad-i" title="新增">{html.escape(r[j1:j2])}</ins>')
    return "".join(parts)


