"""按逐条决定合并终稿，列出排版（空白）差异，生成人可读的 Markdown 审阅记录（#18）。

接口说明见 docs/export.md。要点：

- AuditReport 只列出修改项，没有未修改句和句间空白的映射，单靠报告不能无损拼接。
  这里用 audit() 同一套对齐，从两份规范化全文重算完整对齐序列（含未修改句），
  先核对其中的修改项与报告逐条一致（对齐类型、两侧位置与文本），再按字符位置拼接。
  全程不做字符串查找或替换。
- 句子之外只剩空白（换行、空格、缩进）。修改句前面的空白跟随该修改的决定；
  其余空白（未修改句前、全文末尾）若两侧不同，就是“排版差异”，按 layout 参数处理：
  "follow"（默认）跟随前一处修改的决定，没有修改时用改稿；"original" / "revised" 统一取一侧。
  默认下：全部采用 == 规范化改稿，全部保留 == 规范化原稿。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .align import _align_items, _anchor
from .decisions import (DECISION_STATUSES, STATUS_ZH, ReviewDataError, check_reviewed_report,
                        decision_errors)
from .text import normalize, split_paragraphs

__all__ = ["merge_decisions", "layout_differences", "document_segments", "review_record_markdown",
           "MergeResult", "LayoutDiff", "MergeError", "PendingDecisions", "decision_errors",
           "PENDING_POLICIES", "LAYOUT_POLICIES"]

PENDING_POLICIES = ("block", "original", "revised")
LAYOUT_POLICIES = ("follow", "original", "revised")


class MergeError(ReviewDataError):
    """报告、文稿或决定不能用于合并。消息为中文，可直接展示给用户。"""


class PendingDecisions(MergeError):
    """仍有待定条目，而调用方要求完成版（pending="block"）。"""

    def __init__(self, ids: list[str]):
        self.ids = ids
        super().__init__(f"还有 {len(ids)} 处待定，不能导出完成版。请先逐条决定，或选择导出草稿。")


@dataclass
class LayoutDiff:
    """一处不属于任何修改的空白差异（未修改句之前或全文末尾）。"""
    index: int                      # 从 1 开始的序号
    original: str                   # 原稿这一段空白
    revised: str                    # 改稿这一段空白
    original_pos: int               # 原稿中的起点
    revised_pos: int                # 改稿中的起点
    after_change: str | None        # 前一处修改的编号（"follow" 时跟随它的决定）
    kinds: list[str]                # 观察到的空白变化，如“空行数量变化”
    markdown_risk: str | None = None  # Markdown 下可能影响结构的说明；纯文本为 None


@dataclass
class MergeResult:
    text: str                       # 合并后的全文（规范化换行）
    draft: bool                     # 有待定条目按策略处理时为 True
    pending_policy: str             # "block" | "original" | "revised"
    counts: dict[str, int]          # 各决定状态的条数（含 pending）
    pending_ids: list[str] = field(default_factory=list)
    layout_policy: str = "follow"
    layout: list[LayoutDiff] = field(default_factory=list)   # 排版差异，含每处实际取用的一侧
    layout_choices: list[str] = field(default_factory=list)  # 与 layout 一一对应："original" / "revised"
    change_spans: dict[str, tuple[int, int]] = field(default_factory=dict, repr=False, compare=False)

    @property
    def layout_gaps(self) -> int:
        return len(self.layout)


# ---------------------------------------------------------------- 校验后的完整对齐序列

@dataclass
class _Seg:
    change: dict | None             # None 表示未修改句
    o_span: tuple[int, int] | None
    r_span: tuple[int, int] | None
    g_o: str | None                 # 本项之前的原稿空白（本项原稿侧为空时为 None）
    g_r: str | None
    o_pos: int                      # 本项前原稿游标
    r_pos: int


def _key(alignment, o, r):
    return alignment, o[0], o[1], r[0], r[1]


def _walk(report: dict, original: str, revised: str) -> tuple[list[_Seg], str, str, int, int]:
    """返回 (序列, 原稿尾部空白, 改稿尾部空白, 尾部起点原稿, 尾部起点改稿)。original / revised 已规范化。"""
    check_reviewed_report(report, original, revised)
    by_key = {}
    for c in report["changes"]:
        o, r = c["original"], c["revised"]
        by_key[_key(c["alignment"], (o["start"], o["end"]), (r["start"], r["end"]))] = c
    items = _align_items(original, revised)
    o_has, r_has = bool(split_paragraphs(original)), bool(split_paragraphs(revised))
    raw = []
    for k, it in enumerate(items):
        o_span = (it.orig[0].start, it.orig[-1].end) if it.orig else None
        r_span = (it.rev[0].start, it.rev[-1].end) if it.rev else None
        if it.kind == "0-1":
            pos, _ = _anchor(items, k, "orig", o_has)
            key = _key("0-1", (pos, pos), r_span)
        elif it.kind == "1-0":
            pos, _ = _anchor(items, k, "rev", r_has)
            key = _key("1-0", o_span, (pos, pos))
        elif it.kind == "equal" or original[o_span[0]:o_span[1]] == revised[r_span[0]:r_span[1]]:
            key = None
        else:
            key = _key(it.kind, o_span, r_span)
        raw.append((key, o_span, r_span))
    keys = [k for k, *_ in raw if k is not None]
    if len(by_key) != len(report["changes"]):
        raise MergeError("记录里有两条修改的位置完全相同（位置重复），无法安全合并。")
    if sorted(keys) != sorted(by_key):
        raise MergeError("记录里的修改与重新对齐的结果不一致（可能来自不同版本的工具或被改动），无法安全合并。")

    def gap(full: str, cur: int, span):
        g = full[cur:span[0]]
        if g.strip():  # 句子之外只应剩空白；否则说明断句漏字，拒绝合并
            raise MergeError("对齐结果没有覆盖全部正文，无法无损合并。")
        return g

    segs, po, pr = [], 0, 0
    for key, o_span, r_span in raw:
        if (o_span and o_span[0] < po) or (r_span and r_span[0] < pr):
            raise MergeError("对齐序列不是单调的，无法按位置合并。")
        segs.append(_Seg(by_key[key] if key else None, o_span, r_span,
                         gap(original, po, o_span) if o_span else None,
                         gap(revised, pr, r_span) if r_span else None, po, pr))
        if o_span:
            po = o_span[1]
        if r_span:
            pr = r_span[1]
    tail_o, tail_r = original[po:], revised[pr:]
    if tail_o.strip() or tail_r.strip():
        raise MergeError("对齐结果没有覆盖全部正文，无法无损合并。")
    return segs, tail_o, tail_r, po, pr


# ---------------------------------------------------------------- 排版差异

_FENCE = re.compile(r"^ {0,3}(```|~~~)", re.M)


def _ws_kinds(a: str, b: str) -> list[str]:
    """描述两段空白之间观察到的差异（只描述，不评判）。"""
    kinds = []
    na, nb = a.count("\n"), b.count("\n")
    if na != nb:
        if min(na, nb) == 0:
            kinds.append("换行与空格互换" if (a if na == 0 else b) else "增删换行")
        else:
            kinds.append("空行数量变化")
    if na and nb and a.rsplit("\n", 1)[1] != b.rsplit("\n", 1)[1]:
        kinds.append("行首缩进变化")
    if not kinds:
        kinds.append("空格或制表符变化")
    return kinds


def _md_risk(kinds: list[str], before: str) -> str | None:
    inside_fence = len(_FENCE.findall(before)) % 2 == 1
    if inside_fence:
        return "位于 Markdown 代码块内，空白可能有实际含义"
    if "行首缩进变化" in kinds:
        return "Markdown 中行首缩进可能改变列表层级或形成代码块"
    if "空行数量变化" in kinds or "增删换行" in kinds or "换行与空格互换" in kinds:
        return "Markdown 中换行和空行可能改变段落、列表或引用的分隔"
    return None


def _layout_list(segs, tail_o, tail_r, po, pr, revised: str, markdown: bool) -> list[tuple[LayoutDiff, int]]:
    """[(差异, 在序列里的位置；尾部为 len(segs))]"""
    out, prev = [], None
    for k, s in enumerate(segs):
        if s.change is None and s.g_o != s.g_r:
            kinds = _ws_kinds(s.g_o, s.g_r)
            d = LayoutDiff(len(out) + 1, s.g_o, s.g_r, s.o_pos, s.r_pos, prev, kinds,
                           _md_risk(kinds, revised[:s.r_span[0]]) if markdown else None)
            out.append((d, k))
        if s.change is not None:
            prev = s.change["id"]
    if tail_o != tail_r:
        kinds = _ws_kinds(tail_o, tail_r)
        out.append((LayoutDiff(len(out) + 1, tail_o, tail_r, po, pr, prev, kinds,
                               _md_risk(kinds, revised) if markdown else None), len(segs)))
    return out


def layout_differences(report: dict, original: str, revised: str, *, markdown: bool = False) -> list[LayoutDiff]:
    """不属于任何修改的空白差异。没有文字修改时（changes 为空）也会列出，避免显示成“完全一致”。"""
    original, revised = normalize(original), normalize(revised)
    segs, tail_o, tail_r, po, pr = _walk(report, original, revised)
    return [d for d, _ in _layout_list(segs, tail_o, tail_r, po, pr, revised, markdown)]


# ---------------------------------------------------------------- 合并

def _choice(c: dict, pending: str) -> tuple[str, str]:
    """返回 (空白跟随的一侧, 采用的文本)。"""
    dec = c["decision"]
    st = dec["status"]
    if st == "pending":
        return ("original", c["original"]["text"]) if pending == "original" else ("revised", c["revised"]["text"])
    if st == "rejected":
        return "original", dec["final_text"]
    return "revised", dec["final_text"]  # accepted / auto_passed / edited


def merge_decisions(report: dict, original: str, revised: str, *, pending: str = "block",
                    layout: str = "follow", markdown: bool = False) -> MergeResult:
    """按 decision 合并出完整终稿。

    original / revised：审计时的两份全文（可以未规范化，函数内统一换行后再核对哈希）。
    pending："block"（默认）有待定就抛 PendingDecisions；"original" / "revised" 把待定处按原句 / 改句处理，
             结果标为草稿。
    layout：排版差异的处理，"follow" / "original" / "revised"，见模块说明。
    记录与文稿不一致、决定不合法、对齐结果与记录不符时抛 MergeError（ReviewDataError 的子类）。
    """
    if pending not in PENDING_POLICIES:
        raise ValueError(f"pending 只能是 {PENDING_POLICIES}，收到 {pending!r}")
    if layout not in LAYOUT_POLICIES:
        raise ValueError(f"layout 只能是 {LAYOUT_POLICIES}，收到 {layout!r}")
    original, revised = normalize(original), normalize(revised)
    try:
        segs, tail_o, tail_r, po, pr = _walk(report, original, revised)
    except MergeError:
        raise
    except ReviewDataError as e:
        raise MergeError(str(e)) from None
    changes = report["changes"]
    counts = Counter(c["decision"]["status"] for c in changes)
    pending_ids = [c["id"] for c in changes if c["decision"]["status"] == "pending"]
    if pending_ids and pending == "block":
        raise PendingDecisions(pending_ids)

    layout_at = {k: d for d, k in _layout_list(segs, tail_o, tail_r, po, pr, revised, markdown)}
    first = next((s.change for s in segs if s.change is not None), None)
    state = _choice(first, pending)[0] if first else "revised"
    out: list[str] = []
    diffs, choices = [], []
    change_spans = {}
    offset = 0

    def free_gap(k: int, g_o: str, g_r: str) -> str:
        side = state if layout == "follow" else layout
        if k in layout_at:
            diffs.append(layout_at[k])
            choices.append(side)
        return g_o if side == "original" else g_r

    for k, s in enumerate(segs):
        if s.change is None:
            block = free_gap(k, s.g_o, s.g_r) + original[s.o_span[0]:s.o_span[1]]
        else:
            side, text = _choice(s.change, pending)
            gap = s.g_o if side == "original" else s.g_r
            if gap is None:  # 编辑过的删除句沿用原稿侧空白
                gap = s.g_o or ""
            if not text:  # 删除句子及其前导空白
                gap = ""
            start = offset + len(gap)
            change_spans[s.change["id"]] = (start, start + len(text))
            block = gap + text
            state = side
        out.append(block)
        offset += len(block)
    out.append(free_gap(len(segs), tail_o, tail_r))

    return MergeResult(text="".join(out), draft=bool(pending_ids), pending_policy=pending,
                       counts={s: counts.get(s, 0) for s in DECISION_STATUSES},
                       pending_ids=pending_ids, layout_policy=layout, layout=diffs, layout_choices=choices,
                       change_spans=change_spans)


# ---------------------------------------------------------------- 文稿视图用的连续片段

def document_segments(report: dict, original: str, revised: str) -> list[dict]:
    """按原文顺序列出全部片段，供“文稿视图”连续显示。

    每项：{"change_id": str | None, "gap": 本项前的空白（取改稿侧，改稿侧为空时取原稿侧）,
          "original": 原稿文本, "revised": 改稿文本, "paragraph": 段落序号（从 0 起，按显示用空白计）}
    末尾另有一项 {"change_id": None, "gap": 改稿尾部空白, "original": "", "revised": "", ...}。
    """
    original, revised = normalize(original), normalize(revised)
    segs, tail_o, tail_r, _, _ = _walk(report, original, revised)
    out, para = [], 0
    for s in segs:
        gap = s.g_r if s.g_r is not None else (s.g_o or "")
        if out and re.search(r"\n\s*\n", gap):
            para += 1
        o = original[s.o_span[0]:s.o_span[1]] if s.o_span else ""
        r = revised[s.r_span[0]:s.r_span[1]] if s.r_span else ""
        out.append({"change_id": s.change["id"] if s.change else None, "gap": gap, "original": o, "revised": r,
                    "paragraph": para})
    out.append({"change_id": None, "gap": tail_r, "original": "", "revised": "", "paragraph": para})
    return out


# ---------------------------------------------------------------- 可读审阅记录

def _fence(text: str) -> str:
    """把任意文本放进代码围栏，围栏比文本里最长的反引号串多一个，避免被提前闭合。"""
    run = longest = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    f = "`" * max(3, longest + 1)
    return f"{f}text\n{text}\n{f}" if text else "（空）"


def visible_whitespace(s: str) -> str:
    """把空白画出来：换行 ⏎、空格 ·、制表符 →、全角空格 □。"""
    return (s.replace(" ", "·").replace("\t", "→").replace("\u3000", "□").replace("\n", "⏎")) or "（无）"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


_LAYOUT_ZH = {"follow": "跟随相邻修改的决定（没有修改时用改稿）", "original": "统一保留原稿排版",
              "revised": "统一采用改稿排版"}


def review_record_markdown(report: dict, result: MergeResult, *, title: str,
                           names: dict[str, str] | None = None, formats: dict[str, str] | None = None,
                           now: str | None = None) -> str:
    """人可读的审阅记录（Markdown），可直接贴进 AI 使用披露。进度恢复仍用 JSON 进度文件。"""
    names, formats = names or {}, formats or {}
    doc, run = report["document"], report["run"]
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    kind = "草稿" if result.draft else "完成版"
    c = result.counts
    lines = [f"# 审阅记录（{kind}）：{_cell(title)}", ""]
    if result.draft:
        how = "保留原句" if result.pending_policy == "original" else "采用改句"
        lines += [f"> **草稿**：还有 {len(result.pending_ids)} 处待定，导出时暂按“{how}”处理，不是最终决定。", ""]
    lines += ["| 项目 | 内容 |", "|---|---|", f"| 生成时间 | {now} |"]
    for side, zh in (("original", "原稿"), ("revised", "改稿")):
        nm = _cell(names.get(side) or "（粘贴文本）")
        lines.append(f"| {zh} | {nm}，{formats.get(side, 'txt')}，{doc[f'{side}_chars']} 字，"
                     f"SHA256 {doc[f'{side}_sha256'][:12]} |")
    mode = "词表规则 + 语义模型" if run["mode"] == "full" else "仅词表规则"
    lines += [f"| 检查方式 | {mode}；工具 {run['tool_version']}，词表 {run['lexicon_version']} |",
              f"| 排版差异 | {result.layout_gaps} 处；处理方式：{_LAYOUT_ZH[result.layout_policy]} |", "",
              "## 决定汇总", "",
              "| 决定 | 条数 |", "|---|---|",
              f"| 共计修改（按句或句组计） | {sum(c.values())} |",
              f"| 采用改句 | {c['accepted']} |", f"| 保留原句 | {c['rejected']} |",
              f"| 自行编辑 | {c['edited']} |", f"| 待定 | {c['pending']} |"]
    if c["auto_passed"]:
        lines.append(f"| 自动通过 | {c['auto_passed']} |")
    lines += ["", "## 逐条记录", ""]
    for ch in report["changes"]:
        dec = ch["decision"]
        st = dec["status"]
        n = int(ch["id"][1:]) if ch["id"][1:].isdigit() else ch["id"]
        group = "重点审阅" if ch["review"]["flagged"] else "其余修改"
        label = STATUS_ZH[st] + ("（草稿暂按" + ("原句" if result.pending_policy == "original" else "改句") + "）"
                                 if st == "pending" else "")
        lines += [f"### 第 {n} 处 · {group} · {label}", ""]
        rules = [r["detail"] for r in ch["review"]["reasons"] if r["source"] != "nli"]
        if rules:
            lines += ["检查依据：" + "；".join(rules), ""]
        if any(r["source"] == "nli" for r in ch["review"]["reasons"]):
            lines += ["模型提示：语义模型判断两句含义可能不同（模型判断，仅供参考）", ""]
        adopted = dec["final_text"] if st != "pending" else _choice(ch, result.pending_policy)[1]
        lines += ["原句：", "", _fence(ch["original"]["text"]), "",
                  "改句：", "", _fence(ch["revised"]["text"]), ""]
        if st in ("edited", "pending"):
            lines += ["采用：" if st == "edited" else "草稿暂用：", "", _fence(adopted), ""]
        if dec.get("note"):
            lines += [f"备注：{dec['note']}".replace("\n", " "), ""]
    if result.layout:
        lines += ["## 排版（空白）差异", "",
                  "下列空白差异不属于任何文字修改。空白用符号显示：换行 ⏎、空格 ·、制表符 →、全角空格 □。", "",
                  "| 序号 | 位置 | 原稿 | 改稿 | 观察到的变化 | 终稿取用 |", "|---|---|---|---|---|---|"]
        for d, side in zip(result.layout, result.layout_choices):
            where = f"第 {int(d.after_change[1:])} 处修改之后" if d.after_change and d.after_change[1:].isdigit() else "文首"
            kinds = "、".join(d.kinds) + (f"；{d.markdown_risk}" if d.markdown_risk else "")
            lines.append(f"| {d.index} | {where} | `{visible_whitespace(d.original)}` | `{visible_whitespace(d.revised)}` "
                         f"| {_cell(kinds)} | {'原稿' if side == 'original' else '改稿'} |")
    return "\n".join(lines).rstrip() + "\n"
