"""文风细分标注（style_taxonomy_v1）的导入核对与 A/B 一致性比较。

标注在 Google 表格里做，每位标注员两个工作表，各自导出成 CSV 后交给本脚本：

    判断表（文风判断）：change_id、original_style_issue、revised_style_issue、annotation_status
    子类表（子类与证据）：change_id、六个子类列、original_evidence、revised_evidence、note

两个子命令：

    check    核对一位标注员的两张表，打印进度和问题；可另存规范化 JSON
    compare  比较 A、B 两人都标完（done）的条目，逐字段给出一致率、κ 和分歧清单

三种“没有”必须分开：
    空白          还没检查，不计入任何比较
    not_present   检查过，原稿和改稿都没有这一类
    uncertain     检查过，拿不准

这套数据只回答“文风判断两人是否一致”，不是内容高风险的参考答案：
不进入 pilot.py measure、不当 needs_review、不用于训练。脚本只读输入文件，输出只写新文件。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

from acceptance_desk.text import normalize

if __package__:
    from .agreement import format_kappa, kappa
    from .experiment_io import file_sha, write_new_files
else:
    # 兼容直接执行以及旧测试用 importlib 按文件加载的入口。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agreement import format_kappa, kappa
    from experiment_io import file_sha, write_new_files

TASK = "style_taxonomy_v1"
OPTION_REVISION = "2026-11-12"  # 这一天新增 not_present

SUBTYPES = ["empty_setup", "redundant_summary", "unsupported_inflation",
            "formulaic_structure", "unnecessary_defensiveness", "chat_format_residue"]
ISSUE_FIELDS = ["original_style_issue", "revised_style_issue"]
ISSUE_VALUES = {"yes", "no", "uncertain"}
STATUS_VALUES = {"done", "needs_context"}
DIRECTION_VALUES = {"added", "worsened", "retained", "improved", "removed", "mixed", "uncertain", "not_present"}
# 方向隐含的“这一侧有没有这一类”：added 只在改稿，removed 只在原稿，其余有方向的两侧都有
ORIGINAL_PRESENT = {"retained", "worsened", "improved", "removed", "mixed"}
REVISED_PRESENT = {"added", "retained", "worsened", "improved", "mixed"}
EVIDENCE_FIELDS = {"original_style_issue": "original_evidence", "revised_style_issue": "revised_evidence"}

JUDGEMENT_COLUMNS = ["original_style_issue", "revised_style_issue", "annotation_status"]
SUBTYPE_COLUMNS = SUBTYPES + ["original_evidence", "revised_evidence", "note"]
COMPARE_FIELDS = ISSUE_FIELDS + SUBTYPES
UNCHECKED = ""  # 空白：没检查


class StyleSheetError(ValueError):
    """输入表的结构问题：缺列、ID 重复或为空、两张表对不上。有这类问题时不输出任何数字。"""


# ---------------------------------------------------------------- 读取

def read_table(path: Path, required: list[str], id_column: str = "change_id") -> dict[str, dict]:
    """读一张导出的 CSV，返回 {ID: 行}。多出来的列（隐藏的上下文、批注）照常读入但不使用。

    原句、改句、证据和备注原样保留；仅编号去首尾空白，枚举值在 load_annotator 里规范化。
    每行格数必须与表头相同，多一格少一格都按错位拒绝。连接器读出的快照省略了行末空单元格时，
    先用 pad-snapshot 生成补齐的派生副本，再交给本函数。
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in [id_column, *required] if c not in header]
        if missing:
            raise StyleSheetError(f"{path.name} 缺少列 {missing}；实际表头为 {header}")
        if len(header) != len(set(header)):
            raise StyleSheetError(f"{path.name} 有重复的列名：{header}")
        rows: dict[str, dict] = {}
        for raw in reader:
            if None in raw or any(v is None for v in raw.values()):
                raise StyleSheetError(f"{path.name} 第 {reader.line_num} 行列数不正确")
            row = {k.strip(): v for k, v in raw.items()}
            cid = row.get(id_column, "").strip()
            row[id_column] = cid
            if not cid and not any(v.strip() for v in row.values()):
                continue  # 表格末尾的整行空白
            if not cid:
                raise StyleSheetError(f"{path.name} 第 {reader.line_num} 行 {id_column} 为空")
            if cid in rows:
                raise StyleSheetError(f"{path.name} 第 {reader.line_num} 行 {id_column} 重复：{cid}")
            rows[cid] = row
    if not rows:
        raise StyleSheetError(f"{path.name} 没有任何数据行")
    return rows


def load_annotator(judgement: Path, subtypes: Path, id_column: str = "change_id") -> dict:
    """读一位标注员的两张表并按 ID 合并。两张表的 ID 集合必须完全相同。"""
    j = read_table(judgement, JUDGEMENT_COLUMNS, id_column)
    s = read_table(subtypes, SUBTYPE_COLUMNS, id_column)
    if set(j) != set(s):
        raise StyleSheetError(f"判断表与子类表的 {id_column} 不一致：只在判断表 {sorted(set(j) - set(s))}，"
                              f"只在子类表 {sorted(set(s) - set(j))}")
    rows = {}
    for cid in j:
        row = {f: j[cid][f].strip().lower() for f in JUDGEMENT_COLUMNS}
        row |= {f: s[cid][f].strip().lower() for f in SUBTYPES}
        row |= {f: s[cid][f] for f in ("original_evidence", "revised_evidence", "note")}  # 证据保留原文大小写
        for side in ("original", "revised"):  # 判断表里若带原句、改句，留着给 --units 核对
            if side in j[cid]:
                row[f"_{side}_text"] = j[cid][side]
        for col in ("context_before", "context_after"):  # 讨论稿用；不参与任何比较
            if col in j[cid]:
                row[f"_{col}"] = j[cid][col]
        rows[cid] = row
    return {"task": TASK, "option_revision": OPTION_REVISION,
            "source": {"judgement": {"file": judgement.name, "sha256": file_sha(judgement)},
                       "subtypes": {"file": subtypes.name, "sha256": file_sha(subtypes)}},
            "rows": rows}


def check_units(data: dict, units: Path, id_column: str = "change_id") -> list[str]:
    """和生成标注单元的那份 CSV 核对：ID 集合相同；判断表若带原句、改句，必须逐字相同。"""
    ref = read_table(units, ["original", "revised"], id_column)
    rows = data["rows"]
    errors = []
    if set(ref) != set(rows):
        errors.append(f"标注表与单元表的 {id_column} 不一致：只在标注表 {sorted(set(rows) - set(ref))}，"
                      f"只在单元表 {sorted(set(ref) - set(rows))}")
    for cid in sorted(set(ref) & set(rows)):
        for side in ("original", "revised"):
            text = rows[cid].get(f"_{side}_text")
            if text is not None and text.replace("\r\n", "\n") != ref[cid][side].replace("\r\n", "\n"):
                errors.append(f"{cid}：{side} 与单元表不同")
    return errors


# ---------------------------------------------------------------- 核对一位标注员

def check_rows(data: dict) -> tuple[list[str], list[str]]:
    """返回（错误, 提醒）。

    错误是填写不完整或取值不合法，必须改；提醒是值得标注员自己再看一眼的组合，脚本不替人判断。
    """
    errors, warnings = [], []
    for cid, row in data["rows"].items():
        status = row["annotation_status"]
        bad = []
        if status and status not in STATUS_VALUES:
            bad.append(f"annotation_status=“{status}”")
        bad += [f"{f}=“{row[f]}”" for f in ISSUE_FIELDS if row[f] and row[f] not in ISSUE_VALUES]
        bad += [f"{f}=“{row[f]}”" for f in SUBTYPES if row[f] and row[f] not in DIRECTION_VALUES]
        if bad:
            errors.append(f"{cid}：取值不在选项里 {'、'.join(bad)}")
            continue
        if status != "done":
            continue  # 空白或 needs_context：不要求填满
        blank = [f for f in ISSUE_FIELDS + SUBTYPES if row[f] == UNCHECKED]
        if blank:
            errors.append(f"{cid}：已标 done，但 {'、'.join(blank)} 是空白（检查后没有请填 not_present）")
        for field, evidence in EVIDENCE_FIELDS.items():
            if row[field] in ("yes", "uncertain") and not row[evidence].strip():
                errors.append(f"{cid}：{field}={row[field]}，但 {evidence} 是空的")
        issues = {row[f] for f in ISSUE_FIELDS}
        directions = {row[f] for f in SUBTYPES}
        if "yes" in issues and directions == {"not_present"}:
            warnings.append(f"{cid}：判断有文风问题，但六个子类都是 not_present")
        for field, implied in (("original_style_issue", ORIGINAL_PRESENT), ("revised_style_issue", REVISED_PRESENT)):
            if issues != {"no"} and row[field] == "no" and directions & implied:  # 两侧都 no 由下一条统一提醒
                warnings.append(f"{cid}：子类方向 {sorted(directions & implied)} 表示{'原稿' if field.startswith('original') else '改稿'}"
                                f"有这一类，但 {field}=no")
        if issues == {"no"} and directions - {"not_present", "uncertain"}:
            warnings.append(f"{cid}：两侧都判 no，但子类里有 {sorted(directions - {'not_present', 'uncertain'})}")
    return errors, warnings


def progress(data: dict) -> Counter:
    return Counter(row["annotation_status"] or "空白" for row in data["rows"].values())


def distribution(data: dict, field: str, ids: list[str] | None = None) -> Counter:
    rows = data["rows"]
    return Counter(rows[i][field] or "空白" for i in (ids if ids is not None else rows))


# ---------------------------------------------------------------- A / B 比较

def compare(a: dict, b: dict) -> dict:
    """只比较两人都标了 done 的条目。空白、needs_context 的条目单独计数，不算分歧也不算一致。"""
    if set(a["rows"]) != set(b["rows"]):
        raise StyleSheetError(f"A、B 的条目不一致：只在 A {sorted(set(a['rows']) - set(b['rows']))}，"
                              f"只在 B {sorted(set(b['rows']) - set(a['rows']))}")
    if (a.get("task"), a.get("option_revision")) != (b.get("task"), b.get("option_revision")):
        raise StyleSheetError("A/B 的任务或选项版本不一致")
    bound = a.get("unit_source") is not None and a.get("unit_source") == b.get("unit_source")
    for cid in a["rows"]:
        for field in ("_original_text", "_revised_text", "_context_before", "_context_after"):
            x, y = a["rows"][cid].get(field), b["rows"][cid].get(field)
            if x is not None and y is not None and normalize(x) != normalize(y):
                raise StyleSheetError(f"{cid}：A/B 原改句或上下文不一致，可能来自不同批次")
            if field in ("_original_text", "_revised_text") and (x is None or y is None) and not bound:
                raise StyleSheetError("A/B 缺少原改句，必须通过 --units 绑定同一批单元再比较")
    ids = sorted(a["rows"])
    both = [i for i in ids if a["rows"][i]["annotation_status"] == b["rows"][i]["annotation_status"] == "done"]
    fields, disagreements = {}, []
    for f in COMPARE_FIELDS:
        va = [a["rows"][i][f] for i in both]
        vb = [b["rows"][i][f] for i in both]
        agree = sum(x == y for x, y in zip(va, vb))
        fields[f] = {"n": len(both), "agree": agree, "kappa": kappa(va, vb),
                     "uncertain_a": va.count("uncertain"), "uncertain_b": vb.count("uncertain"),
                     "a": dict(Counter(va)), "b": dict(Counter(vb))}
        for i, x, y in zip(both, va, vb):
            if x != y:
                ev = EVIDENCE_FIELDS.get(f)
                disagreements.append({
                    "change_id": i, "field": f, "a": x, "b": y,
                    "a_original_evidence": a["rows"][i]["original_evidence"],
                    "a_revised_evidence": a["rows"][i]["revised_evidence"],
                    "b_original_evidence": b["rows"][i]["original_evidence"],
                    "b_revised_evidence": b["rows"][i]["revised_evidence"],
                    "a_note": a["rows"][i]["note"], "b_note": b["rows"][i]["note"],
                    "focus": ev or "",
                    "裁决": "", "裁决理由": "",
                })
    return {"task": TASK, "total": len(ids), "compared": len(both),
            "identity_check": "units_and_available_text" if bound else "paired_text",
            "sources": {"a": a.get("source"), "b": b.get("source"), "units": a.get("unit_source")},
            "progress_a": dict(progress(a)), "progress_b": dict(progress(b)),
            "fields": fields, "disagreements": disagreements}


# ---------------------------------------------------------------- 快照适配

# 读取接口（如表格连接器按区间取值）会省略行末的空单元格，序列化后这一行比表头短。
# 只有缺的恰好是表头末尾、且属于下列可选文字列时，才能确认缺的是空格子；判断列和证据列一律不补。
PADDABLE_TRAILING = ("note",)


def pad_snapshot(source: Path, out: Path, paddable: tuple[str, ...] = PADDABLE_TRAILING) -> dict:
    """把省略了行末空单元格的快照补齐成满列的派生副本。原快照只读，派生副本只写新文件。

    补齐的格子一律是空字符串；其余每一格逐字照抄，写完后重读核对。行比表头长、缺的不是末尾可选列，都报错。
    """
    raw = source.read_bytes()
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise StyleSheetError(f"{source.name} 是空文件")
    header, body = rows[0], rows[1:]
    padded_rows, padded_cells = 0, Counter()
    out_rows = [header]
    for n, row in enumerate(body, start=2):
        if len(row) > len(header):
            raise StyleSheetError(f"{source.name} 第 {n} 行比表头多 {len(row) - len(header)} 格，不是省略空格子能解释的")
        missing = header[len(row):]
        if missing and not all(col.strip() in paddable for col in missing):
            raise StyleSheetError(f"{source.name} 第 {n} 行缺 {missing}，只能补末尾的可选列 {list(paddable)}")
        if missing:
            padded_rows += 1
            padded_cells.update(col.strip() for col in missing)
        out_rows.append(row + [""] * len(missing))
    _write_new(out, _rows_csv(out_rows))
    back = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8-sig"), newline="")))
    if back != out_rows or [r[:len(o)] for r, o in zip(back[1:], body)] != body:
        out.unlink()
        raise StyleSheetError(f"{out.name} 写出后与源快照逐格核对不一致")
    return {"source": source.name, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "derived": out.name, "derived_sha256": file_sha(out),
            "rows": len(body), "padded_rows": padded_rows, "padded_cells": dict(padded_cells)}


# ---------------------------------------------------------------- 讨论稿

_SIDE_ZH = {"original_style_issue": "原稿一侧", "revised_style_issue": "改稿一侧"}


def _question(field: str, x: str, y: str) -> str:
    if field in _SIDE_ZH:
        return (f"{_SIDE_ZH[field]}有没有文风问题？A 判 `{x}`，B 判 `{y}`。"
                "如果有，落在哪一句、哪几个字，属于哪个子类？")
    return (f"`{field}`：A 记 `{x}`，B 记 `{y}`。这一类在原稿、改稿里各有没有？"
            "两侧都没有记 `not_present`；有的话方向记哪一项？")


def _quote(text: str) -> str:
    """原文按引用块原样给出，换行保留，不做截断。"""
    if not text:
        return "> （无）"
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def discussion_markdown(a: dict, b: dict, result: dict) -> str:
    """按单元汇总分歧，给两人讨论用：只列两人各自的原判断和证据，以及待回答的问题，不替任何一方下结论。"""
    by_unit: dict[str, list[dict]] = {}
    for d in result["disagreements"]:
        by_unit.setdefault(d["change_id"], []).append(d)
    src = lambda data: "；".join(f"{v['file']} sha256 {v['sha256'][:12]}" for v in data["source"].values())
    lines = [
        f"# 文风细分标注分歧讨论稿（{TASK}，选项修订 {OPTION_REVISION}）",
        "",
        f"- 比较条目：两人都标 done 的 {result['compared']} / {result['total']} 条；有分歧的单元 {len(by_unit)} 个，"
        f"分歧 {len(result['disagreements'])} 处（单元 × 字段）。",
        f"- A：{src(a)}",
        f"- B：{src(b)}",
        "- 本文只列两人原判断、证据和待回答的问题，不代任何一方选择。裁决写在每个问题下面，或另存；不改两人的原始答案。",
        "- 文风判断不是内容高风险答案，不作训练标签。",
        "",
    ]
    for cid in sorted(by_unit):
        ra, rb = a["rows"][cid], b["rows"][cid]
        lines += [f"## {cid}", ""]
        for label, key in (("前文", "_context_before"), ("原稿", "_original_text"),
                           ("改稿", "_revised_text"), ("后文", "_context_after")):
            if key in ra:
                lines += [f"**{label}**", "", _quote(ra[key]), ""]
        lines += ["| 字段 | A | B |", "|---|---|---|"]
        for f in COMPARE_FIELDS:
            mark = " ⚑" if ra[f] != rb[f] else ""
            lines.append(f"| {f}{mark} | {ra[f] or '（空白）'} | {rb[f] or '（空白）'} |")
        lines.append("")
        for who, r in (("A", ra), ("B", rb)):
            lines += [f"**{who} 的证据与备注**", ""]
            for label, field in (("原稿证据", "original_evidence"), ("改稿证据", "revised_evidence"), ("备注", "note")):
                lines += [f"- {label}：", "", _quote(r[field]), ""]
        lines += ["**待回答**", ""]
        for n, d in enumerate(by_unit[cid], 1):
            lines += [f"{n}. {_question(d['field'], d['a'], d['b'])}", "   - 裁决：", "   - 理由：", ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- 裁决导入

_UNIT_HEAD = re.compile(r"^## (\S+)\s*$")
_QUESTION = re.compile(r"^(\d+)\. (.+?)\s*$")
_VERDICT = re.compile(r"^\s+- 裁决：(.*)$")
_REASON = re.compile(r"^\s+- 理由：(.*)$")


def parse_filled_discussion(text: str) -> dict[str, dict]:
    """读回填过的讨论稿：{单元: {"table": {字段: (A, B)}, "items": [{n, question, verdict, reason}]}}。

    只认 discussion_markdown 生成的结构。表格允许被编辑器重新对齐空格；问题原文、裁决和理由各占一行。
    """
    units: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        m = _UNIT_HEAD.match(line)
        if m:
            if m.group(1) in units:
                raise StyleSheetError(f"讨论稿里单元 {m.group(1)} 出现两次")
            cur = units[m.group(1)] = {"table": {}, "items": []}
            continue
        if cur is None:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            field = cells[0].replace("⚑", "").strip() if cells else ""
            if len(cells) == 3 and field in COMPARE_FIELDS:
                cur["table"][field] = (cells[1], cells[2])
            continue
        m = _QUESTION.match(line)
        if m:
            cur["items"].append({"n": int(m.group(1)), "question": m.group(2), "verdict": None, "reason": None})
            continue
        for pattern, key in ((_VERDICT, "verdict"), (_REASON, "reason")):
            m = pattern.match(line)
            if m and cur["items"]:
                if cur["items"][-1][key] is not None:
                    raise StyleSheetError(f"{line.strip()[:20]}… 在同一问题下出现两次")
                cur["items"][-1][key] = m.group(1).strip()
    return units


def apply_adjudication(a: dict, b: dict, filled_text: str) -> dict:
    """把回填的裁决逐题对回由 A、B 快照重算出的分歧，返回裁决记录与 30 单元合并标签。

    不改 A、B 的原始答案，也不改独立一致性统计：合并标签只是“一致处沿用、分歧处采用裁决”的派生结果。
    """
    for name, data in (("A", a), ("B", b)):
        errors, _ = check_rows(data)
        pending = [cid for cid, row in data["rows"].items() if row["annotation_status"] != "done"]
        if errors or pending:
            raise StyleSheetError(f"{name} 尚未完成全部标注或有填写错误，不能导出完整合并标签：" +
                                  "；".join([*errors, *pending]))
    result = compare(a, b)
    expected: dict[str, list[dict]] = {}
    for d in result["disagreements"]:
        expected.setdefault(d["change_id"], []).append(d)
    filled = parse_filled_discussion(filled_text)
    errors = []
    if set(filled) != set(expected):
        errors.append(f"讨论稿的单元与重算的分歧单元不一致：只在讨论稿 {sorted(set(filled) - set(expected))}，"
                      f"只在重算 {sorted(set(expected) - set(filled))}")
    items = []
    for cid in sorted(set(filled) & set(expected)):
        got, want = filled[cid], expected[cid]
        for f in COMPARE_FIELDS:  # 表里两人的值必须与快照一致（只比值，不比排版）
            shown = got["table"].get(f)
            truth = (a["rows"][cid][f] or "（空白）", b["rows"][cid][f] or "（空白）")
            if shown is not None and shown != truth:
                errors.append(f"{cid} {f}：讨论稿表内 A/B 为 {shown}，快照为 {truth}")
        if len(got["items"]) != len(want):
            errors.append(f"{cid}：讨论稿有 {len(got['items'])} 个问题，重算分歧 {len(want)} 处")
            continue
        for k, (item, d) in enumerate(zip(got["items"], want), 1):
            where = f"{cid} 第 {k} 题（{d['field']}）"
            if item["n"] != k or item["question"] != _question(d["field"], d["a"], d["b"]):
                errors.append(f"{where}：问题编号或原文与模板不一致")
                continue
            allowed = ISSUE_VALUES if d["field"] in ISSUE_FIELDS else DIRECTION_VALUES
            verdict = (item["verdict"] or "").strip().strip("`").lower()
            if verdict not in allowed:
                errors.append(f"{where}：裁决“{item['verdict'] or ''}”不是合法取值 {sorted(allowed)}")
                continue
            if not item["reason"]:
                errors.append(f"{where}：没有写理由")
                continue
            items.append({"change_id": cid, "field": d["field"], "a": d["a"], "b": d["b"], "verdict": verdict,
                          "same_as": "A" if verdict == d["a"] else "B" if verdict == d["b"] else "neither",
                          "reason": item["reason"]})
    if errors:
        raise StyleSheetError("裁决稿核对未通过：\n  " + "\n  ".join(errors))
    decided = {(i["change_id"], i["field"]): i["verdict"] for i in items}
    merged = {}
    for cid in sorted(a["rows"]):
        row = {"change_id": cid}
        for f in COMPARE_FIELDS:
            x, y = a["rows"][cid][f], b["rows"][cid][f]
            row[f] = decided.get((cid, f), x if x == y else None)
            if row[f] is None:  # 分歧却没有裁决：上面已经报错，这里只是防御
                raise StyleSheetError(f"{cid} {f} 有分歧但没有裁决")
        row["adjudicated_fields"] = ";".join(f for f in COMPARE_FIELDS if (cid, f) in decided)
        merged[cid] = row
    # 合并后的标签用同一套机械检查再过一遍：只提醒方向与两侧判断的矛盾，不改判
    check = {"rows": {cid: {**r, "annotation_status": "done", "original_evidence": "x", "revised_evidence": "x",
                            "note": ""} for cid, r in merged.items()}}
    errors, warnings = check_rows(check)
    if errors:
        raise StyleSheetError("合并标签未通过核对：" + "；".join(errors))
    return {"items": items, "merged": merged, "warnings": warnings, "compare": result}


# ---------------------------------------------------------------- 输出

def _write_new(path: Path, text: str) -> None:
    """只写新文件：已有的文件（包括两人的原始导出）一律不覆盖。"""
    write_new_files([(path, text)])


def bind_units(a: dict, b: dict, units: Path, id_column: str) -> list[str]:
    errors = [f"{name} {e}" for name, data in (("A", a), ("B", b))
              for e in check_units(data, units, id_column)]
    if not errors:
        source = {"file": units.name, "sha256": file_sha(units)}
        a["unit_source"] = b["unit_source"] = source
    return errors


def _csv_text(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _rows_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _print_progress(name: str, data: dict) -> None:
    p = progress(data)
    print(f"{name}：共 {len(data['rows'])} 条｜done {p.get('done', 0)}｜needs_context {p.get('needs_context', 0)}"
          f"｜空白 {p.get('空白', 0)}")


def cmd_check(args) -> int:
    data = load_annotator(args.judgement, args.subtypes, args.id_column)
    errors, warnings = check_rows(data)
    if args.units:
        errors = check_units(data, args.units, args.id_column) + errors
    _print_progress(args.name, data)
    for e in errors:
        print(f"  [错误] {e}")
    for w in warnings:
        print(f"  [提醒] {w}")
    if not errors and not warnings:
        print("  没有发现填写问题。")
    if args.out:
        if errors:
            print("[错误] 有填写问题，没有写出 JSON", file=sys.stderr)
            return 1
        public = {k: v for k, v in data.items()} | {
            "annotator": args.name, "note": "文风细分标注；不是内容高风险的参考答案",
            "rows": {cid: {k: v for k, v in row.items() if not k.startswith("_")} for cid, row in data["rows"].items()}}
        _write_new(args.out, json.dumps(public, ensure_ascii=False, indent=2) + "\n")
        print(f"已写入 {args.out}")
    return 1 if errors else 0


def _read_pair(args):
    a = load_annotator(args.a_judgement, args.a_subtypes, args.id_column)
    b = load_annotator(args.b_judgement, args.b_subtypes, args.id_column)
    errors = [f"A {e}" for e in check_rows(a)[0]] + [f"B {e}" for e in check_rows(b)[0]]
    if args.units:
        errors += bind_units(a, b, args.units, args.id_column)
    return a, b, errors


def cmd_compare(args) -> int:
    a, b, errors = _read_pair(args)
    if errors:
        print("[错误] 先修正填写问题再比较：\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    result = compare(a, b)
    _print_progress("A", a)
    _print_progress("B", b)
    n = result["compared"]
    print(f"\n两人都标 done 的条目：{n} / {result['total']}。只比较这些条目；空白和 needs_context 不算一致也不算分歧。")
    if n == 0:
        return 0
    print("\n| 字段 | 一致 / 比较 | κ | A 填 uncertain | B 填 uncertain |")
    print("|---|---|---|---|---|")
    for f, r in result["fields"].items():
        print(f"| {f} | {r['agree']} / {r['n']} | {format_kappa(r['kappa'])} | {r['uncertain_a']} | {r['uncertain_b']} |")
    print("\n各字段两人的取值分布（用来看 κ 低是分布太偏还是真有分歧）：")
    for f, r in result["fields"].items():
        print(f"- {f}：A {dict(sorted(r['a'].items()))}；B {dict(sorted(r['b'].items()))}")
    print(f"\n分歧 {len(result['disagreements'])} 处（按条目 × 字段计）。"
          f"样本只有 {n} 条，κ 只用来校准口径，不说明哪一类该删或产品成不成立。")
    outputs = []
    if args.disagreements:
        cols = list(result["disagreements"][0]) if result["disagreements"] else ["change_id", "field", "a", "b"]
        outputs.append((args.disagreements, _csv_text(cols, result["disagreements"])))
    if args.discussion:
        outputs.append((args.discussion, discussion_markdown(a, b, result)))
    write_new_files(outputs)
    for path, _ in outputs:
        print(f"已写入 {path}（新文件；两人的原始导出没有改动）")
    return 0


def cmd_pad(args) -> int:
    info = pad_snapshot(args.source, args.out)
    print(f"{info['source']} → {info['derived']}：{info['rows']} 行，补齐 {info['padded_rows']} 行 {info['padded_cells'] or '（无）'}")
    print(f"  源快照 sha256 {info['source_sha256']}（未改动）")
    print(f"  派生副本 sha256 {info['derived_sha256']}")
    return 0


def cmd_adjudicate(args) -> int:
    if not args.decided_by.strip() or args.decided_by != args.decided_by.strip():
        raise StyleSheetError("裁决人必须为无首尾空白的非空文本")
    a, b, errors = _read_pair(args)
    if errors:
        print("[错误] 快照本身有填写问题：\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    filled_bytes = args.filled.read_bytes()
    out = apply_adjudication(a, b, filled_bytes.decode("utf-8-sig"))
    items, merged = out["items"], out["merged"]
    inputs = {"a": a["source"], "b": b["source"],
              "filled_discussion": {"file": args.filled.name, "sha256": hashlib.sha256(filled_bytes).hexdigest()}}
    if args.units:
        inputs["units"] = {"file": args.units.name, "sha256": file_sha(args.units)}
    record = {
        "task": TASK, "option_revision": OPTION_REVISION, "decided_by": args.decided_by,
        "note": "分歧裁决；一致处沿用两人相同的值。不是内容高风险答案，不作训练标签；两人原始答案和独立一致性统计不因此改动。",
        "inputs": inputs,
        "independent_agreement": {f: {"agree": r["agree"], "n": r["n"], "kappa": r["kappa"]}
                                  for f, r in out["compare"]["fields"].items()},
        "items": items, "merged_warnings": out["warnings"],
    }
    write_new_files([
        (args.record, json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"),
        (args.merged, _csv_text(["change_id", *COMPARE_FIELDS, "adjudicated_fields"], list(merged.values()))),
    ])
    same = Counter(i["same_as"] for i in items)
    print(f"裁决 {len(items)} 项：与 A 相同 {same.get('A', 0)}，与 B 相同 {same.get('B', 0)}，两者都不同 {same.get('neither', 0)}")
    print(f"合并标签 {len(merged)} 个单元，其中 {sum(bool(r['adjudicated_fields']) for r in merged.values())} 个含裁决字段")
    for w in out["warnings"]:
        print(f"  [提醒] 合并后 {w}")
    print(f"裁决记录：{args.record}\n合并标签：{args.merged}")
    print("独立一致性统计按原始 A/B 计算，不因裁决改变；合并标签不重新算 κ。")
    return 0


def _add_pair_arguments(p):
    p.add_argument("a_judgement", type=Path)
    p.add_argument("a_subtypes", type=Path)
    p.add_argument("b_judgement", type=Path)
    p.add_argument("b_subtypes", type=Path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文风细分标注（style_taxonomy_v1）的核对与 A/B 比较")
    ap.add_argument("--id-column", default="change_id", help="单元编号所在的列，默认 change_id")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="核对一位标注员的两张表")
    p.add_argument("judgement", type=Path, help="“文风判断”工作表导出的 CSV")
    p.add_argument("subtypes", type=Path, help="“子类与证据”工作表导出的 CSV")
    p.add_argument("--name", default="标注员")
    p.add_argument("--units", type=Path, help="生成标注单元的 CSV（如 p01_dg03_pilot_A.csv），核对编号与原改句")
    p.add_argument("--out", type=Path, help="把规范化结果写成新 JSON 文件（已存在则拒绝）")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("adjudicate", help="读回填过的讨论稿，生成裁决记录与合并标签（只写新文件）")
    _add_pair_arguments(p)
    p.add_argument("filled", type=Path, help="回填了“裁决”“理由”的讨论稿")
    p.add_argument("--units", type=Path)
    p.add_argument("--decided-by", required=True, help="裁决人，如实填写（例如“timnie”）；不写成双方共同签字")
    p.add_argument("--record", type=Path, required=True, help="裁决记录 JSON（新文件）")
    p.add_argument("--merged", type=Path, required=True, help="合并标签 CSV（新文件）")
    p.set_defaults(func=cmd_adjudicate)

    p = sub.add_parser("pad-snapshot", help="把省略了行末空单元格的快照补成满列的派生副本（只补末尾 note 列）")
    p.add_argument("source", type=Path, help="原快照 CSV（只读）")
    p.add_argument("out", type=Path, help="派生副本路径（必须是新文件）")
    p.set_defaults(func=cmd_pad)

    p = sub.add_parser("compare", help="比较 A、B 两人都标完的条目")
    _add_pair_arguments(p)
    p.add_argument("--units", type=Path)
    p.add_argument("--disagreements", type=Path, help="把分歧清单写成新 CSV（已存在则拒绝）")
    p.add_argument("--discussion", type=Path, help="按单元汇总的分歧讨论稿，写成新 Markdown 文件（已存在则拒绝）")
    p.set_defaults(func=cmd_compare)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (StyleSheetError, OSError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
