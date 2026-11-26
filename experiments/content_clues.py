"""把标注材料里已有的内容判断线索整理成可追溯清单，并核对来源和覆盖。

文风标注表、裁决理由、准备者的对齐审查里，常顺带写到事实、归因、范围或论断强度的变化。
这些话是人写的，可以复用；但哪些算“已有结论”、哪些只是“待核实”，要逐条标明，不能升级成金标准。

清单是一个 JSON 文件（由整理者手写，放在仓库外）：

    {"clues": [{"id": "k01", "unit": "u016", "source": {"kind": "annotator", "who": "B", "field": "note"},
                "quote": "……标注者原话的一段……", "topic": "归因", "status": "pending", "note": "……"}]}

- source.kind：annotator（标注表的某个单元格）、adjudication（裁决理由）、preparer（准备者文件里的一段）
- status：stated（标注者明确写出的判断）、pending（标注者只指出需要核实或复核）、conflict（两人对同一点说法相反）
- quote 必须能在来源单元格（或准备者文件）里逐字找到；脚本不判断语义，只核对出处和统计覆盖

两个子命令：

    check   核对每条线索的出处、取值和单元；打印样本单元与完整分区两层的覆盖
    render  生成给人看的 Markdown：按单元列出原改文、每条线索的原话与状态，以及没有任何线索的单元

不涉及的单元记为“未涉及”：表里没写不等于检查过，文风判断为 no 也不等于内容无风险。

作者回答（可选）：问题清单回填后，整理者另写一个回答清单 JSON，逐条说明哪段作者原话回答了哪几条线索、怎么回答的：

    {"resolutions": [{"id": "r01", "unit": "u016", "clues": ["k03", "k04"], "quote": "……作者原话……",
                      "needs_review": "是", "outcome": "resolved", "note": "……"}]}

- outcome：resolved（原话直接回答了这条线索指出的点）、partly_resolved（只回答了其中一部分）、
  other_reason（作者给了需要看的理由，但不是这条线索指出的那一点）、needs_clarification（回答与线索方向对不上，
  要作者解释）、author_undecided（作者明确写了待定）
- quote 必须能在回填文件里该单元的段落中逐字找到；needs_review 必须与该段“是否需要作者看”一栏逐字相同
- 回填文件除“结论”“是否需要作者看”两栏外，其余各行必须与问题清单模板逐字相同
- 一个单元可以有多条线索：一段笼统的回答不会自动算作解决了该单元的全部线索，没被任何回答指到的待核实线索仍算未答
- 补充回答（--supplement，每个单元一行，如“1. u042：……”）：条目写 "source": "supplement"，原话必须在该单元那一行里；
  补充回答没有“是否需要作者看”一栏，needs_review 写 null，不推断。同一线索可以有多条回答，早先的回答不被覆盖；
  后来的回答用 "supersedes": ["r05"] 指明取代哪一条，被取代的仍保留在记录与清单里，只是不再参与最终状态
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

STATUSES = {"stated": "明确写出的判断", "pending": "待核实", "conflict": "两人说法相反"}
SOURCE_KINDS = {"annotator", "adjudication", "preparer"}
OUTCOMES = {"resolved": "已回答", "partly_resolved": "部分回答", "other_reason": "给了别的理由",
            "needs_clarification": "需澄清", "author_undecided": "作者待定"}
_ANSWER_FIELDS = ("- 结论：", "- 是否需要作者看：")
_SUPPLEMENT_LINE = re.compile(r"^\s*\d+\.\s+([^\s：:（(]+)\s*[：:]")  # “1. u042：……”
_ANSWER_HEAD = re.compile(r"^## \d+\.\s+([^\s（(]+)")  # “## 1. u007（样本内）”


class ClueError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_rows(path: Path) -> dict[str, dict]:
    """按 ID 读一张标注 CSV。行末被读取接口省略的空格子按空白补（只用于找原话，不改源文件）。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ClueError(f"{path.name} 是空文件")
    header = rows[0]
    out = {}
    for row in rows[1:]:
        if len(row) > len(header):
            raise ClueError(f"{path.name} 有一行比表头长")
        rec = dict(zip(header, row + [""] * (len(header) - len(row))))
        out[rec[header[0]].strip()] = rec
    return out


def load_sources(annotators: dict[str, list[Path]], adjudication: Path | None, preparer: list[Path]) -> dict:
    tables = {who: {} for who in annotators}
    for who, paths in annotators.items():
        for p in paths:
            for uid, rec in _strict_rows(p).items():
                tables[who].setdefault(uid, {}).update(rec)
    adj = {}
    if adjudication:
        for item in json.loads(adjudication.read_text(encoding="utf-8"))["items"]:
            adj.setdefault(item["change_id"], []).append(item["reason"])
    prep = {p.name: p.read_text(encoding="utf-8") for p in preparer}
    files = [p for ps in annotators.values() for p in ps] + ([adjudication] if adjudication else []) + list(preparer)
    return {"tables": tables, "adjudication": adj, "preparer": prep,
            "hashes": {p.name: _sha256(p) for p in files}}


def check_clues(clues: list[dict], sources: dict, unit_ids: set[str]) -> list[str]:
    errors, seen = [], set()
    for c in clues:
        cid = c.get("id", "")
        where = f"线索 {cid or '（无编号）'}"
        if not cid or cid in seen:
            errors.append(f"{where}：编号为空或重复")
        seen.add(cid)
        if c.get("unit") not in unit_ids:
            errors.append(f"{where}：单元 {c.get('unit')} 不在分区里")
        if c.get("status") not in STATUSES:
            errors.append(f"{where}：status 必须是 {sorted(STATUSES)} 之一")
        quote, src = c.get("quote", ""), c.get("source", {})
        if not quote.strip():
            errors.append(f"{where}：quote 为空")
            continue
        kind = src.get("kind")
        if kind not in SOURCE_KINDS:
            errors.append(f"{where}：source.kind 必须是 {sorted(SOURCE_KINDS)} 之一")
            continue
        if kind == "annotator":
            cell = sources["tables"].get(src.get("who"), {}).get(c.get("unit"), {}).get(src.get("field"))
            if cell is None:
                errors.append(f"{where}：找不到 {src.get('who')} 在 {c.get('unit')} 的 {src.get('field')} 单元格")
            elif quote not in cell:
                errors.append(f"{where}：原话不在 {src.get('who')}.{src.get('field')} 里（必须逐字摘录）")
        elif kind == "adjudication":
            if not any(quote in r for r in sources["adjudication"].get(c.get("unit"), [])):
                errors.append(f"{where}：原话不在该单元的裁决理由里")
        else:
            text = sources["preparer"].get(src.get("file", ""))
            if text is None:
                errors.append(f"{where}：没有提供准备者文件 {src.get('file')}")
            elif quote not in text:
                errors.append(f"{where}：原话不在 {src.get('file')} 里")
    return errors


def coverage(clues: list[dict], sample: list[str], partition: list[str]) -> dict:
    """样本单元和完整分区分开统计；只数“有没有被人写到”，不推全文比例。"""
    by_unit: dict[str, Counter] = {}
    for c in clues:
        by_unit.setdefault(c["unit"], Counter())[c["status"]] += 1
    def layer(units: list[str]) -> dict:
        touched = [u for u in units if u in by_unit]
        return {"units": len(units), "touched": len(touched), "untouched": len(units) - len(touched),
                "with_pending": sum(by_unit[u]["pending"] > 0 for u in touched),
                "with_conflict": sum(by_unit[u]["conflict"] > 0 for u in touched),
                "only_stated": sum(set(by_unit[u]) == {"stated"} for u in touched)}
    outside = sorted(set(by_unit) - set(sample))
    return {"sample": layer(sample), "partition": layer(partition), "touched_outside_sample": outside,
            "by_status": dict(Counter(c["status"] for c in clues))}


def check_answer_template(template: str, answers: str) -> list[str]:
    """回填文件除两栏答案外，必须与问题清单模板逐行相同（不能改题、删题或挪动线索）。"""
    t, a = template.splitlines(), answers.splitlines()
    if len(t) != len(a):
        return [f"回填文件 {len(a)} 行，模板 {len(t)} 行：题目或线索被增删"]
    errors = []
    for n, (x, y) in enumerate(zip(t, a), 1):
        field = next((f for f in _ANSWER_FIELDS if x.startswith(f)), None)
        if field:
            if not y.startswith(field):
                errors.append(f"第 {n} 行应为“{field}”一栏")
        elif x != y:
            errors.append(f"第 {n} 行与模板不同（只能填两栏答案）")
    return errors


def answer_sections(answers: str) -> dict[str, str]:
    """按“## n. 单元编号”切出每个单元的回答段落。"""
    sections, cur = {}, None
    for line in answers.splitlines():
        if line.startswith("## "):
            m = _ANSWER_HEAD.match(line)
            cur = m.group(1) if m else None
            if cur:
                if cur in sections:
                    raise ClueError(f"回填文件里单元 {cur} 出现两次")
                sections[cur] = ""
            continue
        if cur:
            sections[cur] += line + "\n"
    return sections


def supplement_lines(text: str) -> dict[str, str]:
    """补充回答文件：每个单元一行，形如“1. u042：……”。返回 {单元编号: 这一行}。"""
    lines = {}
    for line in text.splitlines():
        m = _SUPPLEMENT_LINE.match(line)
        if m:
            if m.group(1) in lines:
                raise ClueError(f"补充回答里单元 {m.group(1)} 出现两次")
            lines[m.group(1)] = line
    return lines


def check_resolutions(resolutions: list[dict], clues: list[dict], sections: dict[str, str],
                      supplement: dict[str, str] | None = None) -> list[str]:
    by_id = {c["id"]: c for c in clues}
    errors, seen = [], set()
    for r in resolutions:
        rid = r.get("id", "")
        where = f"回答 {rid or '（无编号）'}"
        if not rid or rid in seen:
            errors.append(f"{where}：编号为空或重复")
        seen.add(rid)
        from_supplement = r.get("source") == "supplement"
        if from_supplement:
            # 补充回答没有“是否需要作者看”一栏：只核对原话在该单元那一行里，不推断 needs_review
            line = (supplement or {}).get(r.get("unit"))
            if line is None:
                errors.append(f"{where}：补充回答里没有单元 {r.get('unit')} 的那一行")
                continue
            sec = line + "\n"
        else:
            sec = sections.get(r.get("unit"))
        unit = r.get("unit")
        if sec is None:
            errors.append(f"{where}：回填文件里没有单元 {unit}")
            continue
        if r.get("outcome") not in OUTCOMES:
            errors.append(f"{where}：outcome 必须是 {sorted(OUTCOMES)} 之一")
        if not r.get("quote", "").strip() or r["quote"] not in sec:
            errors.append(f"{where}：原话不在该单元的回填段落里（必须逐字摘录）")
        if from_supplement:
            if r.get("needs_review") is not None:
                errors.append(f"{where}：补充回答没有“是否需要作者看”一栏，needs_review 应为 null")
        elif f"{_ANSWER_FIELDS[1]}{r.get('needs_review', '')}\n" not in sec:
            errors.append(f"{where}：needs_review 与该单元“是否需要作者看”一栏不一致")
        ids = r.get("clues") or []
        for old_id in r.get("supersedes", []):
            prev = next((x for x in resolutions if x.get("id") == old_id), None)
            if prev is None or prev is r or not set(prev.get("clues") or []) & set(ids) or prev.get("unit") != unit:
                errors.append(f"{where}：supersedes 的 {old_id} 不存在，或与本条不是同一单元、同一线索")
        if not ids:
            errors.append(f"{where}：没有指明回答了哪几条线索")
        for cid in ids:
            c = by_id.get(cid)
            if c is None or c["unit"] != unit:
                errors.append(f"{where}：线索 {cid} 不存在或不属于单元 {unit}")
            elif c["status"] != "pending":
                errors.append(f"{where}：线索 {cid} 原本不是待核实，不需要回答")
    return errors


def resolution_state(clues: list[dict], resolutions: list[dict]) -> dict[str, list[str]]:
    """每条待核实线索被哪些回答以什么方式回应；没有被任何回答指到的记为 unanswered。

    被后来的回答 supersedes 的回答仍保留在记录里，只是不再参与最终状态。
    """
    state = {c["id"]: [] for c in clues if c["status"] == "pending"}
    superseded = {x for r in resolutions for x in r.get("supersedes", [])}
    for r in resolutions:
        if r["id"] in superseded:
            continue
        for cid in r["clues"]:
            state[cid].append(r["outcome"])
    return {cid: (outs or ["unanswered"]) for cid, outs in state.items()}


def _final(outcomes: list[str]) -> str:
    """同一条线索有多种回应时取最需要人跟进的那一种。"""
    for key in ("needs_clarification", "author_undecided", "unanswered", "other_reason", "partly_resolved", "resolved"):
        if key in outcomes:
            return key
    return outcomes[0]


LABELS = ("semantic_change", "stronger_claim", "weaker_claim", "ai_slop", "no_risk")
_YES, _NO = {"1", "是", "yes", "y", "true"}, {"0", "否", "no", "n", "false"}


def read_labels(path: Path, sample: list[str]) -> tuple[dict[str, dict], list[str]]:
    """读一份已填的五分类标注表（pilot.py sheet 的格式）。编号必须正好是样本单元，label 只能是五个代码之一。"""
    rows = _strict_rows(path)
    errors = []
    if set(rows) != set(sample):
        errors.append(f"标注表的编号与样本不一致：只在标注表 {sorted(set(rows) - set(sample))}，只在样本 {sorted(set(sample) - set(rows))}")
    for uid, r in rows.items():
        label = (r.get("label") or "").strip()
        if label not in LABELS:
            errors.append(f"标注表 {uid}：label 填的是“{label}”，应为五个代码之一")
        nr = (r.get("needs_review") or "").strip().lower()
        if nr and nr not in _YES | _NO:
            errors.append(f"标注表 {uid}：needs_review 填的是“{nr}”")
    return rows, errors


def label_conflicts(labels: dict[str, dict], resolutions: list[dict]) -> list[dict]:
    """已有五分类标签与作者回答之间机械可判的冲突。

    只算两种：作者确认这处有内容问题（resolved / partly_resolved）而标签是 no_risk；作者写“需要看：是”而标注者填了不需要看。
    类别不同（例如作者说“过强”、标签是 semantic_change）都属于有风险，不算冲突；作者给的是文风理由时也不算。
    """
    superseded = {x for r in resolutions for x in r.get("supersedes", [])}
    live = [r for r in resolutions if r["id"] not in superseded]
    out = []
    for uid, row in sorted(labels.items()):
        label = row["label"].strip()
        nr = (row.get("needs_review") or "").strip().lower()
        for r in (r for r in live if r["unit"] == uid):
            if r["outcome"] in ("resolved", "partly_resolved") and label == "no_risk":
                out.append({"unit": uid, "kind": "作者确认有内容问题，标签为 no_risk", "label": label,
                            "needs_review": nr, "author_quote": r["quote"], "resolution": r["id"]})
            if r.get("needs_review") == "是" and nr in _NO:
                out.append({"unit": uid, "kind": "作者写需要看，标注者填不需要看", "label": label,
                            "needs_review": nr, "author_quote": r["quote"], "resolution": r["id"]})
    return out


def _unit_texts(units: list[dict], report: dict) -> dict[str, dict]:
    changes = {c["id"]: c for c in report["changes"]}
    out = {}
    for u in units:
        members = [changes[i] for i in u["change_ids"]]
        out[u["id"]] = {"change_ids": u["change_ids"],
                        "original": "\n".join(m["original"]["text"] for m in members if m["original"]["text"]),
                        "revised": "\n".join(m["revised"]["text"] for m in members if m["revised"]["text"])}
    return out


def render(clues: list[dict], cov: dict, texts: dict[str, dict], sample: list[str], hashes: dict,
           resolutions: list[dict] | None = None) -> str:
    who_zh = {"annotator": "标注者", "adjudication": "裁决理由", "preparer": "准备者"}
    lines = ["# 已有内容判断线索（私有）", "",
             "- 状态：" + "；".join(f"`{k}` {v}" for k, v in STATUSES.items()) + "。状态由整理者按原话措辞标注，不是人工判断。",
             "- 只摘录人已经写下的话。没有线索的单元记为“未涉及”：没写不等于检查过，文风判断为 no 也不等于内容无风险。",
             f"- 样本 {cov['sample']['units']} 个单元：{cov['sample']['touched']} 个有线索，{cov['sample']['untouched']} 个未涉及。"
             f"完整分区 {cov['partition']['units']} 个单元：{cov['partition']['touched']} 个有线索。样本里的比例不外推到全文。",
             "- 来源哈希：" + "；".join(f"{k} {v[:12]}" for k, v in sorted(hashes.items())), ""]
    by_unit: dict[str, list[dict]] = {}
    for c in clues:
        by_unit.setdefault(c["unit"], []).append(c)
    for uid in sorted(by_unit):
        t = texts[uid]
        tag = "样本内" if uid in sample else "样本外（定向）"
        lines += [f"## {uid}（{tag}，成员 {'+'.join(t['change_ids'])}）", "",
                  "**原稿**", "", *("> " + x for x in t["original"].split("\n")), "",
                  "**改稿**", "", *("> " + x for x in t["revised"].split("\n")), ""]
        for c in by_unit[uid]:
            s = c["source"]
            src = f"{who_zh[s['kind']]} {s.get('who', '')}{'.' + s['field'] if s.get('field') else ''}{s.get('file', '')}".strip()
            lines += [f"- `{c['id']}`｜{c.get('topic', '')}｜**{c['status']}**｜{src}", f"  - 原话：{c['quote']}"]
            if c.get("note"):
                lines.append(f"  - 整理说明：{c['note']}")
            for r in resolutions or []:
                if c["id"] in r["clues"]:
                    via = "补充回答" if r.get("source") == "supplement" else "作者回答"
                    if any(r["id"] in x.get("supersedes", []) for x in resolutions):
                        via += "，已被后续回答取代"
                    nr = "未写" if r.get("needs_review") is None else r["needs_review"]
                    lines.append(f"  - {via}（{OUTCOMES[r['outcome']]}，需要作者看：{nr}）：{r['quote']}")
                    if r.get("note"):
                        lines.append(f"    - 整理说明：{r['note']}")
            if resolutions is not None and c["status"] == "pending" and not any(c["id"] in r["clues"] for r in resolutions):
                lines.append("  - 作者回答：没有回答指到这一条（仍待核实）")
        lines.append("")
    untouched = [u for u in sample if u not in by_unit]
    lines += ["## 样本内未涉及内容的单元", "", "、".join(untouched) or "（无）", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="已有内容判断线索的出处核对与覆盖统计")
    ap.add_argument("clues", type=Path)
    ap.add_argument("--a", type=Path, nargs="+", required=True, help="标注者 A 的 CSV（可多张，按 ID 合并）")
    ap.add_argument("--b", type=Path, nargs="+", required=True, help="标注者 B 的 CSV")
    ap.add_argument("--adjudication", type=Path, help="style_labels.py adjudicate 生成的裁决记录")
    ap.add_argument("--preparer", type=Path, nargs="*", default=[], help="准备者文件（如对齐审查、作者裁决对照）")
    ap.add_argument("--units", type=Path, required=True, help="完整单元分区 JSON")
    ap.add_argument("--sample", type=Path, required=True, help="样本单元表（任一标注 CSV，取第一列编号）")
    ap.add_argument("--report", type=Path, help="审计报告；给出时 render 附原改文")
    ap.add_argument("--render", type=Path, help="把可读清单写成新 Markdown 文件")
    ap.add_argument("--resolutions", type=Path, help="作者回答清单 JSON（需同时给 --answers 与 --template）")
    ap.add_argument("--answers", type=Path, help="回填了“结论 / 是否需要作者看”的问题清单（只读）")
    ap.add_argument("--template", type=Path, help="当初发出的问题清单模板，用来核对回填文件没有改题")
    ap.add_argument("--labels", type=Path, help="已填的五分类标注表（复用旧标签）；需同时给作者回答")
    ap.add_argument("--conflicts", type=Path, help="把标签与作者回答的冲突写成新 CSV")
    ap.add_argument("--supplement", type=Path, help="补充回答文件（每个单元一行）；回答清单里 source=supplement 的条目在这里核对")
    args = ap.parse_args(argv)
    try:
        clues = json.loads(args.clues.read_text(encoding="utf-8"))["clues"]
        units = json.loads(args.units.read_text(encoding="utf-8"))["units"]
        partition = [u["id"] for u in units]
        sample = list(_strict_rows(args.sample))
        if not set(sample) <= set(partition):
            raise ClueError("样本单元不全在分区里")
        sources = load_sources({"A": args.a, "B": args.b}, args.adjudication, args.preparer)
        errors = check_clues(clues, sources, set(partition))
        if errors:
            print("[错误] 线索清单核对未通过：\n  " + "\n  ".join(errors), file=sys.stderr)
            return 1
        cov = coverage(clues, sample, partition)
        print(f"线索 {len(clues)} 条，全部能在来源里逐字找到；按状态 {cov['by_status']}")
        for name, key in (("样本", "sample"), ("完整分区", "partition")):
            c = cov[key]
            print(f"{name} {c['units']} 个单元：有线索 {c['touched']}（含待核实 {c['with_pending']}、含冲突 {c['with_conflict']}、"
                  f"只有明确判断 {c['only_stated']}），未涉及 {c['untouched']}")
        if cov["touched_outside_sample"]:
            print(f"样本外有线索的单元（定向材料）：{cov['touched_outside_sample']}")
        resolutions = None
        if args.resolutions or args.answers or args.template:
            if not (args.resolutions and args.answers and args.template):
                raise ClueError("--resolutions、--answers、--template 必须同时给出")
            answers = args.answers.read_text(encoding="utf-8")
            errors = check_answer_template(args.template.read_text(encoding="utf-8"), answers)
            resolutions = json.loads(args.resolutions.read_text(encoding="utf-8"))["resolutions"]
            supplement = supplement_lines(args.supplement.read_text(encoding="utf-8")) if args.supplement else None
            if supplement is None and any(r.get("source") == "supplement" for r in resolutions):
                raise ClueError("回答清单里有补充回答条目，需要 --supplement")
            errors += check_resolutions(resolutions, clues, answer_sections(answers), supplement)
            if errors:
                print("[错误] 作者回答核对未通过：\n  " + "\n  ".join(errors), file=sys.stderr)
                return 1
            state = resolution_state(clues, resolutions)
            final = Counter(_final(v) for v in state.values())
            print(f"待核实线索 {len(state)} 条的回应：" + "，".join(f"{OUTCOMES.get(k, '没有回答')} {n}" for k, n in sorted(final.items())))
            unit_of = {c["id"]: c["unit"] for c in clues}
            done = {u for u in {unit_of[c] for c in state}
                    if all(_final(state[c]) == "resolved" for c in state if unit_of[c] == u)}
            for name, ids in (("样本", set(sample)), ("完整分区", set(partition))):
                pend_units = {unit_of[c] for c in state} & ids
                print(f"{name}：含待核实线索的 {len(pend_units)} 个单元里，全部线索都已回答的 {len(done & ids)} 个")
            sources["hashes"] |= {args.answers.name: _sha256(args.answers), args.resolutions.name: _sha256(args.resolutions)}
            if args.supplement:
                sources["hashes"][args.supplement.name] = _sha256(args.supplement)
        if args.labels:
            if resolutions is None:
                raise ClueError("--labels 需要同时给作者回答（--resolutions、--answers、--template）")
            labels, errors = read_labels(args.labels, sample)
            if errors:
                print("[错误] 标注表核对未通过：\n  " + "\n  ".join(errors), file=sys.stderr)
                return 1
            conflicts = label_conflicts(labels, resolutions)
            dist = Counter(r["label"].strip() for r in labels.values())
            print(f"标注表 {len(labels)} 个单元全部有标签；分布 {dict(sorted(dist.items()))}")
            print(f"与作者回答的冲突 {len(conflicts)} 处（类别不同不算冲突）")
            for c in conflicts:
                print(f"  {c['unit']}：{c['kind']}（label={c['label']}，needs_review={c['needs_review'] or '空'}）")
            if args.conflicts:
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=["unit", "kind", "label", "needs_review", "author_quote", "resolution", "裁决", "理由"],
                                   lineterminator="\n")
                w.writeheader()
                w.writerows({**c, "裁决": "", "理由": ""} for c in conflicts)
                with open(args.conflicts, "x", encoding="utf-8-sig", newline="") as f:
                    f.write(buf.getvalue())
                print(f"冲突清单：{args.conflicts}（新文件）")
        if args.render:
            if not args.report:
                raise ClueError("render 需要 --report 以附原改文")
            texts = _unit_texts(units, json.loads(args.report.read_text(encoding="utf-8")))
            with open(args.render, "x", encoding="utf-8") as f:
                f.write(render(clues, cov, texts, sample, sources["hashes"] | {args.clues.name: _sha256(args.clues)},
                               resolutions))
            print(f"可读清单：{args.render}（新文件）")
        return 0
    except (ClueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
