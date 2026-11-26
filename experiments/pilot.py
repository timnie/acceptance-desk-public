"""试标与前置测量工具（Issue #3、#15）。

四个子命令，按使用顺序：

    sheet      原稿 + 改稿 → 两份空白标注表（A、B 各一份）和一份答案钥匙
    kappa      两份填好的标注表 → Cohen's κ 与分歧清单
    selfcheck  大模型自检的回答 → 它点到了哪些修改
    measure    汇总 #15 的三个数字

标注表里只有原句、改句和上下文，不含系统的候选原因（标注手册 §1）。
系统是否把某条标为“重点审阅”单独存在钥匙文件里，标注完成前不要打开。

所有输入输出都含论文原文，只放 data/ 下已被 .gitignore 忽略的目录。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from itertools import combinations
from contextlib import ExitStack
from pathlib import Path

from acceptance_desk import audit
from acceptance_desk._schema import text_sha256
from acceptance_desk.text import normalize, split_sentences

if __package__:
    from .agreement import format_kappa, kappa
    from .pilot_units import UNIT_VERSION, digest, measurement_view, check_unit_key, unit_hits
    from .report_checks import check_source_report
else:
    # 同时支持直接执行与 importlib 按文件加载（旧回归用例的入口）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agreement import format_kappa, kappa
    from pilot_units import UNIT_VERSION, digest, measurement_view, check_unit_key, unit_hits
    from report_checks import check_source_report

LABELS = ["semantic_change", "stronger_claim", "weaker_claim", "ai_slop", "no_risk"]
LABEL_NAMES = dict(zip(LABELS, ["语义篡改", "论证变强", "论证变弱", "AI slop", "无风险"]))

# 标注手册 §6 的列，加上试标用的二分类列 needs_review（Issue #16）
SHEET_COLUMNS = [
    "change_id", "original_start", "original_end", "revised_start", "revised_end",
    "original", "revised", "context_before", "context_after",
    "label", "second_choice", "needs_review", "note", "annotator",
]

_YES = {"1", "是", "y", "yes", "true"}
_NO = {"0", "否", "n", "no", "false"}


# ---------------------------------------------------------------- 读写

def read_sheet(path: Path) -> dict[str, dict]:
    """读完整标注表，拒绝空表、缺列、空 ID 和重复 ID，避免字典静默覆盖。"""
    rows = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(SHEET_COLUMNS) - set(reader.fieldnames or [])
        if reader.fieldnames and len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"{path.name} 有重复的列名")
        if missing:
            raise ValueError(f"{path.name} 缺少列：{', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{path.name} 第 {reader.line_num} 行列数不正确")
            cid = row["change_id"].strip()
            if not cid or cid in rows:
                raise ValueError(f"{path.name} 第 {reader.line_num} 行 change_id 为空或重复：{cid!r}")
            rows[cid] = dict(row, change_id=cid)
    if not rows:
        raise ValueError(f"{path.name} 是空标注表")
    return rows


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # 带 BOM，Excel 打开中文不乱码
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def parse_needs_review(row: dict) -> bool | None:
    """needs_review 列：1/0、是/否都认；空着返回 None。"""
    value = (row.get("needs_review") or "").strip().lower()
    if value in _YES:
        return True
    if value in _NO:
        return False
    return None


def check_sheet(rows: dict[str, dict], name: str) -> list[str]:
    """填表检查：标签代码是否合法；needs_review 列可留空（#16 起二分类由标签推出），填了就必须是 1 / 0。"""
    errors = []
    for cid, row in rows.items():
        label = (row.get("label") or "").strip()
        second = (row.get("second_choice") or "").strip()
        if label not in LABELS:
            errors.append(f"{name} {cid}：label 填的是“{label}”，应为 {' / '.join(LABELS)} 之一")
        if second and second not in LABELS:
            errors.append(f"{name} {cid}：second_choice 填的是“{second}”")
        if (row.get("needs_review") or "").strip() and parse_needs_review(row) is None:
            errors.append(f"{name} {cid}：needs_review 不是 1 / 0")
    return errors


def check_pair(sheet_a: dict, sheet_b: dict) -> list[str]:
    """两人必须标同一批条目，原句、改句和位置也必须一致。"""
    errors = check_sheet(sheet_a, "A 表") + check_sheet(sheet_b, "B 表")
    if not sheet_a or not sheet_b or set(sheet_a) != set(sheet_b):
        errors.append("两份标注表为空或 change_id 不一致")
    fields = ("original", "revised", "original_start", "original_end", "revised_start", "revised_end")
    for cid in set(sheet_a) & set(sheet_b):
        if any(sheet_a[cid].get(k) != sheet_b[cid].get(k) for k in fields):
            errors.append(f"{cid}：A/B 表的原句、改句或位置不一致")
    return errors


def check_measure_inputs(report: dict, key: dict, sheet_a: dict, sheet_b: dict) -> None:
    """核对四份输入的身份和抽样计数，失败时不输出任何测量结果。"""
    errors = check_pair(sheet_a, sheet_b)
    items = key["items"]
    if not items or set(sheet_a) != set(items) or set(sheet_b) != set(items):
        errors.append("两份标注表必须完整对应钥匙中的全部 change_id")
    for side in ("original", "revised"):
        name = side + "_sha256"
        digest = key.get(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != report["document"].get(name):
            errors.append(f"报告与钥匙的 {name} 不一致或缺失")
    if key.get("mode") != report["run"]["mode"]:
        errors.append("报告与钥匙的运行模式不一致")
    changes = {c["id"]: c for c in report["changes"]}
    if len(changes) != len(report["changes"]) or not set(items) <= set(changes):
        errors.append("报告有重复 ID 或缺少钥匙中的条目")
    if type(key.get("total_changes")) is not int or key["total_changes"] != len(changes):
        errors.append("报告与钥匙的修改总数不一致")
    for group, flag in (("flagged", True), ("unflagged", False)):
        counts = key["groups"][group]
        population = sum(c["review"]["flagged"] == flag for c in changes.values())
        sampled = sum(item["flagged"] == flag for item in items.values())
        if any(type(counts.get(k)) is not int for k in ("population", "sampled")) or counts != {"population": population, "sampled": sampled}:
            errors.append(f"钥匙的 {group} 总体或抽样计数与报告不一致")
        if population and not sampled:
            errors.append(f"{group} 总体非空但没有标注样本，不能估计风险")
    for cid, item in items.items():
        change = changes.get(cid)
        if change is None:
            continue
        if type(item.get("flagged")) is not bool or item["flagged"] != change["review"]["flagged"]:
            errors.append(f"{cid}：钥匙的重点审阅标记与报告不一致")
        for name, sheet in (("A", sheet_a), ("B", sheet_b)):
            if cid not in sheet:
                continue
            row = sheet[cid]
            for side in ("original", "revised"):
                expected = change[side]
                if row[side] != expected["text"] or any(row[f"{side}_{pos}"] != str(expected[pos]) for pos in ("start", "end")):
                    errors.append(f"{name} 表 {cid}：{side} 文本或位置与报告不一致")
    if errors:
        raise ValueError("测量输入不一致：\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------- sheet

def _context(change: dict, orig_sents: list, rev_sents: list) -> tuple[str, str]:
    """取这处修改前后各一句作上下文。有改句就在改稿里取，整句删除时在原稿里取。"""
    if change["revised"]["text"]:
        side, sents = change["revised"], rev_sents
    else:
        side, sents = change["original"], orig_sents
    before = [s.text for s in sents if s.end <= side["start"]]
    after = [s.text for s in sents if s.start >= side["end"]]
    return (before[-1] if before else ""), (after[0] if after else "")


def sample_changes(changes: list[dict], n: int, seed: int, min_per_group: int = 10) -> list[dict]:
    """分层抽样：按“重点审阅 / 其余修改”两组，各组按比例分配名额，但每组至少 min_per_group 条。

    为什么分层：如果“其余修改”里一条都没抽到，就没法估计系统漏掉了多少。
    每组的总数和抽样数写进钥匙文件，measure 用它们加权还原全章的比例。
    """
    if type(n) is not int or n < 1:
        raise ValueError("抽样数 n 必须为正整数")
    if type(seed) is not int:
        raise ValueError("抽样种子必须为整数（不能是 bool）")
    if type(min_per_group) is not int or min_per_group < 1:
        raise ValueError("min_per_group 必须为正整数")
    if n < len({c["review"]["flagged"] for c in changes}):
        raise ValueError("抽样数必须能覆盖所有非空分层")
    if len(changes) <= n:
        return list(changes)
    groups = {True: [c for c in changes if c["review"]["flagged"]],
              False: [c for c in changes if not c["review"]["flagged"]]}
    quota = {}
    for flag, members in groups.items():
        share = round(n * len(members) / len(changes))
        quota[flag] = min(len(members), max(share, min_per_group))
    # 两组名额相加可能不等于 n：从名额多的一组里补或减
    while quota[True] + quota[False] != n:
        step = 1 if quota[True] + quota[False] < n else -1
        candidates = [f for f in (True, False)
                      if (step == 1 and quota[f] < len(groups[f])) or (step == -1 and quota[f] > 1)]
        flag = max(candidates, key=lambda f: len(groups[f]) - quota[f] if step == 1 else quota[f])
        quota[flag] += step
    rng = random.Random(seed)
    picked = rng.sample(groups[True], quota[True]) + rng.sample(groups[False], quota[False])
    return sorted(picked, key=lambda c: c["id"])  # 按原文顺序排，方便对着上下文读


def cmd_sheet(args) -> None:
    original = normalize(args.original.read_text(encoding="utf-8-sig"))
    revised = normalize(args.revised.read_text(encoding="utf-8-sig"))
    report = (json.loads(args.report.read_text(encoding="utf-8-sig")) if args.report
              else audit(original, revised, mode=args.mode))
    check_documents(report, {"original": original, "revised": revised})
    check_source_report(report, original, revised)
    unit_metadata = {}
    view = report
    if args.unit_map:
        partition = json.loads(args.unit_map.read_text(encoding="utf-8-sig"))
        if partition.get("report_sha256") != digest(report):
            raise ValueError("单元分区必须绑定当前报告的 report_sha256；请同时传 --report")
        view = measurement_view(report, {"original": original, "revised": revised}, partition["units"])
        unit_metadata = {"unit_version": UNIT_VERSION, "units": partition["units"],
                         "report_sha256": digest(report), "total_raw_changes": len(report["changes"]),
                         "sampling_design": "stratified_random"}
    changes = view["changes"]
    if not changes:
        sys.exit("[错误] 原稿和改稿之间没有任何修改")
    orig_sents, rev_sents = split_sentences(original), split_sentences(revised)
    picked = sample_changes(changes, args.n, args.seed)

    rows = []
    for c in picked:
        before, after = _context(c, orig_sents, rev_sents)
        rows.append({
            "change_id": c["id"],
            "original_start": c["original"]["start"], "original_end": c["original"]["end"],
            "revised_start": c["revised"]["start"], "revised_end": c["revised"]["end"],
            "original": c["original"]["text"], "revised": c["revised"]["text"],
            "context_before": before, "context_after": after,
            "label": "", "second_choice": "", "needs_review": "", "note": "", "annotator": "",
        })

    out, name = args.out_dir, args.name
    if not name or name in (".", "..") or Path(name).name != name:
        raise ValueError("name 必须是文件名前缀，不能含路径")

    # 原稿里被改动的句子数：2-1 合句占两句，0-1 新增不占原稿句子
    changed = sum({"1-1": 1, "1-2": 1, "2-1": 2, "1-0": 1, "0-1": 0}[c["alignment"]] for c in report["changes"])
    flagged_all = sum(c["review"]["flagged"] for c in changes)
    key = {
        "name": name, "seed": args.seed, "mode": report["run"]["mode"],
        "sampling_design": "stratified_random", "report_sha256": digest(report),
        "original_sha256": report["document"]["original_sha256"],
        "revised_sha256": report["document"]["revised_sha256"],
        "original_sentences": len(orig_sents), "changed_original_sentences": changed,
        "total_changes": len(changes),
        "groups": {  # population = 全章条数，sampled = 进入标注表的条数
            "flagged": {"population": flagged_all,
                        "sampled": sum(c["review"]["flagged"] for c in picked)},
            "unflagged": {"population": len(changes) - flagged_all,
                          "sampled": sum(not c["review"]["flagged"] for c in picked)},
        },
        **unit_metadata,
        "items": {c["id"]: {"flagged": c["review"]["flagged"],
                            **({"change_ids": c["change_ids"]} if unit_metadata else {}),
                            "reasons": [r["code"] for r in c["review"]["reasons"]]} for c in picked},
    }
    # 先独占预留全部路径，任何冲突均不覆盖已有表，也不留下半批输出。
    out.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with ExitStack() as stack:
            handles = []
            for suffix in ("pilot_A.csv", "pilot_B.csv", "pilot_key.json", "report.json"):
                path = out / f"{name}_{suffix}"
                handles.append(stack.enter_context(path.open("x", encoding="utf-8-sig" if suffix.endswith("csv") else "utf-8", newline="")))
                created.append(path)
            for f, annotator in zip(handles, ("A", "B")):
                writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
                writer.writeheader()
                writer.writerows([{**r, "annotator": annotator} for r in rows])
            handles[2].write(json.dumps(key, ensure_ascii=False, indent=2) + "\n")
            handles[3].write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except Exception:
        for path in created:
            path.unlink()
        raise

    print(f"模式 {report['run']['mode']}｜全章修改 {len(changes)} 处｜抽出 {len(picked)} 条")
    print(f"标注表：{out / (name + '_pilot_A.csv')}、{out / (name + '_pilot_B.csv')}")
    print(f"钥匙与完整报告（标注完成前不要打开）：{name}_pilot_key.json、{name}_report.json")


def cmd_units(args) -> None:
    from acceptance_desk import review_units
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    documents = read_documents(args, report)
    units = review_units(report, documents["original"], documents["revised"], min_block=args.min_block)
    partition = {"unit_version": UNIT_VERSION, "report_sha256": digest(report), "units": units,
                 "method": {"name": "review_units", "min_block": args.min_block},
                 "note": "自动归组候选；不含风险答案，实际标注前需核对边界"}
    with args.out.open("x", encoding="utf-8") as f:
        json.dump(partition, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- kappa

def agreement_table(sheet_a: dict, sheet_b: dict) -> tuple[list[dict], list[dict]]:
    """返回（一致性表, 分歧清单）。只比较两份表里都有的 change_id。"""
    ids = sorted(set(sheet_a) & set(sheet_b))
    la = [sheet_a[i]["label"].strip() for i in ids]
    lb = [sheet_b[i]["label"].strip() for i in ids]
    table = []
    for label in LABELS:  # 每一类单独当成“是 / 否”两分类
        ya, yb = [x == label for x in la], [x == label for x in lb]
        table.append({"类别": LABEL_NAMES[label], "A": sum(ya), "B": sum(yb), "kappa": kappa(ya, yb)})
    table.append({"类别": "总体（五分类）", "A": len(ids), "B": len(ids), "kappa": kappa(la, lb)})
    # 两种二分类：一种由五分类标签推出（不是无风险就算高风险），一种是标注员直接填的 needs_review
    ha, hb = [x != "no_risk" for x in la], [x != "no_risk" for x in lb]
    table.append({"类别": "高风险（由标签推出）", "A": sum(ha), "B": sum(hb), "kappa": kappa(ha, hb)})
    na = [parse_needs_review(sheet_a[i]) for i in ids]
    nb = [parse_needs_review(sheet_b[i]) for i in ids]
    if None not in na and None not in nb:  # 旧表另填了 needs_review 时才报这一行，只作对照
        table.append({"类别": "是否需要人看（直接填，仅对照）", "A": sum(bool(x) for x in na), "B": sum(bool(x) for x in nb),
                      "kappa": kappa(na, nb)})

    disagreements = []
    for i, x, y, p, q in zip(ids, la, lb, na, nb):
        if x != y or (p is not None and q is not None and p != q):
            disagreements.append({
                "change_id": i, "original": sheet_a[i]["original"], "revised": sheet_a[i]["revised"],
                "label_A": x, "label_B": y,
                "needs_review_A": "" if p is None else int(p), "needs_review_B": "" if q is None else int(q),
                "note_A": sheet_a[i].get("note", ""), "note_B": sheet_b[i].get("note", ""),
                "原因（手册没写清 / 看漏 / 确实模棱两可）": "",
            })
    return table, disagreements


def cmd_kappa(args) -> None:
    a, b = read_sheet(args.sheet_a), read_sheet(args.sheet_b)
    errors = check_pair(a, b)
    if errors:
        sys.exit("[错误] 标注表有问题，先改再算：\n  " + "\n  ".join(errors))
    table, disagreements = agreement_table(a, b)
    print(f"共 {len(a)} 条，分歧 {len(disagreements)} 条\n")
    print("| 类别 | 条数（A 标） | 条数（B 标） | κ | κ ≥ 0.6 |")
    print("|---|---|---|---|---|")
    for row in table:
        k = row["kappa"]
        verdict = "—" if k is None else ("是" if k >= 0.6 else "否")
        print(f"| {row['类别']} | {row['A']} | {row['B']} | {format_kappa(k)} | {verdict} |")
    if args.out:
        columns = list(disagreements[0]) if disagreements else ["change_id"]
        with args.out.open("x", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(disagreements)
        print(f"\n分歧清单：{args.out}")


# ---------------------------------------------------------------- selfcheck

_QUOTE_LINE = re.compile(r"^\s*(?:[-*\d.、）)]+\s*)?(改句|删句)\s*[:：]\s*(.+?)\s*$")


def parse_selfcheck(answer: str) -> list[tuple[str, str]]:
    """从大模型的回答里取出它引用的句子：[(“改句”或“删句”, 句子)]。

    只认提示词要求的“改句：……”“删句：……”两种行，说明文字不参与匹配。
    """
    quotes = []
    for line in answer.splitlines():
        m = _QUOTE_LINE.match(line)
        if m:
            quotes.append((m.group(1), m.group(2).strip("“”\"「」")))
    return quotes


def match_quote(kind: str, quote: str, changes: list[dict], documents: dict[str, str] | None = None) -> str | None:
    """引文必须在该侧全文中只有一个整句区间，并且完整落在一处修改里。"""
    side = "revised" if kind == "改句" else "original"
    if documents is None:
        return None  # 修改列表不能排除全文其他位置的同句，未提供全文时不自动计入
    full = documents[side]
    sentences = split_sentences(full)
    ends = {s.end for s in sentences}
    spans = [(s.start, s.start + len(quote)) for s in sentences
             if s.start + len(quote) in ends and full.startswith(quote, s.start)]
    if len(spans) != 1:
        return None
    start, end = spans[0]
    matches = []
    for c in changes:
        target = c[side]
        text = target["text"]
        if not text or (kind == "删句" and c["revised"]["text"]):
            continue
        if target["start"] <= start and end <= target["end"]:
            matches.append(c["id"])
    return matches[0] if len(matches) == 1 else None


def selfcheck_hits(answer: str, changes: list[dict], confirmed: dict[int, set[str]] | None = None,
                   documents: dict[str, str] | None = None) -> tuple[set[str], list[str]]:
    """返回（被点到的 change_id 集合, 对不上的引用）。"""
    hits, unmatched = set(), []
    for index, (kind, quote) in enumerate(parse_selfcheck(answer), 1):
        if confirmed and index in confirmed:
            hits.update(confirmed[index])
            continue
        cid = match_quote(kind, quote, changes, documents)
        if cid:
            hits.add(cid)
        else:
            unmatched.append(f"{kind}：{quote}")
    return hits, unmatched


MAPPING_COLUMNS = ["answer_sha256", "report_sha256", "original_sha256", "revised_sha256", "quote_index",
                   "kind", "quote", "change_id", "note"]


# 映射文件和单元钥匙沿用同一种报告摘要。
report_digest = digest


def check_documents(report: dict, documents: dict[str, str] | None) -> None:
    """只接受与报告指纹及修改区间一致的全文，不把全文写入报告或映射。"""
    if documents is None or set(documents) != {"original", "revised"}:
        raise ValueError("自检核对需要 --original 和 --revised 全文")
    for side, full in documents.items():
        if text_sha256(full) != report["document"][side + "_sha256"]:
            raise ValueError(f"{side} 全文与报告的文稿哈希不一致")
        for change in report["changes"]:
            target = change[side]
            start, end = target["start"], target["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(full) or full[start:end] != target["text"]:
                raise ValueError(f"{change['id']} 的 {side} 区间与全文不一致")


def read_documents(args, report: dict) -> dict[str, str]:
    if args.original is None or args.revised is None:
        raise ValueError("自检核对需要 --original 和 --revised 全文")
    documents = {side: normalize(getattr(args, side).read_text(encoding="utf-8-sig")) for side in ("original", "revised")}
    check_documents(report, documents)
    return documents


def selfcheck_runs(report: dict, paths: list[Path], mapping_path: Path | None = None,
                   documents: dict[str, str] | None = None) -> list[tuple[Path, set[str], list[str]]]:
    """人工映射单独保存，以原始回答哈希、文档哈希和引用序号绑定，原始回答只读。"""
    check_documents(report, documents)
    mappings = []
    if mapping_path:
        with mapping_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if len(reader.fieldnames or []) != len(MAPPING_COLUMNS) or set(reader.fieldnames or []) != set(MAPPING_COLUMNS):
                raise ValueError("人工映射 CSV 的列必须为：" + ",".join(MAPPING_COLUMNS))
            mappings = list(reader)
        if not mappings:
            raise ValueError("人工映射 CSV 为空")
    changes = {c["id"]: c for c in report["changes"]}
    used, seen, runs = set(), set(), []
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        answer = raw.decode("utf-8-sig")
        quotes = parse_selfcheck(answer)
        confirmed = {}
        for number, row in enumerate(mappings):
            if row.get("answer_sha256") != digest:
                continue
            if None in row or any(row.get(k) is None for k in MAPPING_COLUMNS):
                raise ValueError("人工映射 CSV 行的列数不正确")
            if any(row[side + "_sha256"] != report["document"][side + "_sha256"] for side in ("original", "revised")):
                raise ValueError("人工映射与报告的文档哈希不一致")
            if row["report_sha256"] != report_digest(report):
                raise ValueError("人工映射不属于这份报告，请重新核对修改位置")
            index = int(row["quote_index"])
            if not 1 <= index <= len(quotes) or tuple(row[k] for k in ("kind", "quote")) != quotes[index - 1]:
                raise ValueError("人工映射的引用序号、类型或原文与原始回答不一致")
            cid = row["change_id"].strip()
            if cid not in changes or not row["note"].strip():
                raise ValueError("人工映射需要有效 change_id 和人工核对说明")
            target = changes[cid]
            if not target["revised" if row["kind"] == "改句" else "original"]["text"] or (row["kind"] == "删句" and target["revised"]["text"]):
                raise ValueError("人工映射的修改类型与目标条目不一致")
            identity = (digest, index, cid)
            if number not in used and identity in seen:
                raise ValueError("人工映射包含重复的回答、引用和 change_id")
            seen.add(identity)
            used.add(number)
            confirmed.setdefault(index, set()).add(cid)
        hits, pending = selfcheck_hits(answer, report["changes"], confirmed, documents)
        runs.append((path, hits, pending))
    if len(used) != len(mappings):
        raise ValueError("人工映射含不属于这些原始回答的记录（请检查文件和 SHA256）")
    return runs


def cmd_selfcheck(args) -> None:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    documents = read_documents(args, report)
    if args.pending_out and args.mapping:
        raise ValueError("请先不带 --mapping 导出待核对表，避免覆盖已有人工确认")
    pending_rows = []
    for path, hits, unmatched in selfcheck_runs(report, args.answers, args.mapping, documents):
        print(f"{path.name}：点到 {len(hits)} 处修改 {sorted(hits)}")
        for u in unmatched:
            print(f"  待人工核对（未计入命中） → {u}")
        if args.pending_out:
            raw = path.read_bytes()
            for index, (kind, quote) in enumerate(parse_selfcheck(raw.decode("utf-8-sig")), 1):
                if match_quote(kind, quote, report["changes"], documents) is None:
                    row = {"answer_sha256": hashlib.sha256(raw).hexdigest(), "report_sha256": report_digest(report),
                           **{side + "_sha256": report["document"][side + "_sha256"] for side in ("original", "revised")},
                           "quote_index": index, "kind": kind, "quote": quote, "change_id": "", "note": ""}
                    if row not in pending_rows:
                        pending_rows.append(row)
    if args.pending_out:
        protected = [args.report, args.original, args.revised, *args.answers]
        if any(args.pending_out.resolve() == p.resolve() for p in protected) or args.pending_out.exists():
            raise ValueError("待核对表必须写入一个新文件，不能覆盖原始回答、报告或已有映射")
        with args.pending_out.open("x", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MAPPING_COLUMNS)
            writer.writeheader()
            writer.writerows(pending_rows)
        print(f"待人工核对表：{args.pending_out}（{len(pending_rows)} 条；填写 change_id 和 note 后用 --mapping 读取）")


# ---------------------------------------------------------------- measure

def gold_labels(sheet_a: dict, sheet_b: dict) -> dict[str, dict]:
    """每条的参考答案（#16：二分类由五分类推出，label != no_risk 即需要看）。

    两种口径：宽（任一人的标签有风险）和严（两人的标签都有风险）。另填的 needs_review 列只作对照，不进参考答案。
    """
    gold = {}
    for cid in set(sheet_a) & set(sheet_b):
        a = sheet_a[cid]["label"].strip() != "no_risk"
        b = sheet_b[cid]["label"].strip() != "no_risk"
        gold[cid] = {"wide": a or b, "strict": a and b}
    return gold


def weighted_estimates(key: dict, gold: dict[str, dict], which: str) -> dict:
    """把分层样本还原成全章的估计。

    每组的高风险比例 = 该组样本里高风险条数 / 该组样本数；
    全章高风险条数 ≈ Σ 每组全章条数 × 该组比例；
    规则筛选的召回率 ≈ “重点审阅”组里的高风险条数 / 全章高风险条数。
    """
    if not gold or set(gold) != set(key["items"]):
        raise ValueError("参考答案必须完整对应钥匙中的全部样本")
    est = {}
    for group, flag in (("flagged", True), ("unflagged", False)):
        ids = [cid for cid, item in key["items"].items() if item["flagged"] == flag and cid in gold]
        risky = sum(gold[cid][which] for cid in ids)
        if key["groups"][group]["population"] and not ids:
            raise ValueError(f"{group} 总体非空但没有标注样本，不能按零风险估计")
        rate = risky / len(ids) if ids else 0.0
        est[group] = {"sampled": len(ids), "risky": risky, "rate": rate,
                      "population": key["groups"][group]["population"],
                      "estimated_risky": key["groups"][group]["population"] * rate}
    total_risky = est["flagged"]["estimated_risky"] + est["unflagged"]["estimated_risky"]
    est["high_risk_rate"] = total_risky / key["total_changes"] if key["total_changes"] else 0.0
    est["flag_recall"] = est["flagged"]["estimated_risky"] / total_risky if total_risky else None
    est["flag_precision"] = est["flagged"]["rate"] if est["flagged"]["sampled"] else None
    return est


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def cmd_measure(args) -> None:
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    key = json.loads(args.key.read_text(encoding="utf-8-sig"))
    documents = read_documents(args, report)
    check_source_report(report, **documents)
    if key.get("sampling_design") != "stratified_random" or key.get("report_sha256") != digest(report):
        raise ValueError("抽样钥匙缺少随机设计或报告指纹证据；旧批次须按原种子和抽样数另行核对，不覆盖原表或手补字段")
    if type(key.get("seed")) is not int:
        raise ValueError("抽样种子缺失或不是整数")
    expected_counts = {
        "original_sentences": len(split_sentences(documents["original"])),
        "changed_original_sentences": sum({"1-1": 1, "1-2": 1, "2-1": 2, "1-0": 1, "0-1": 0}[c["alignment"]]
                                          for c in report["changes"]),
    }
    if any(type(key.get(k)) is not int or key[k] != v for k, v in expected_counts.items()):
        raise ValueError("原稿句子规模与抽样钥匙不一致")
    sheet_a, sheet_b = read_sheet(args.sheet_a), read_sheet(args.sheet_b)
    view = report
    if "unit_version" in key:
        view = check_unit_key(report, key, documents)
    replay = sample_changes(view["changes"], len(key["items"]), key["seed"])
    if {c["id"] for c in replay} != set(key["items"]):
        raise ValueError("样本与登记种子的分层随机抽样结果不一致")
    check_measure_inputs(view, key, sheet_a, sheet_b)
    answer_runs = selfcheck_runs(report, args.selfcheck, args.mapping, documents) if args.selfcheck or args.mapping else []
    if "unit_version" in key:
        answer_runs = [(path, unit_hits(hits, key["units"]), pending) for path, hits, pending in answer_runs]
    gold = gold_labels(sheet_a, sheet_b)
    g = key["groups"]
    pct = lambda x: "无法计算" if x is None else f"{100 * x:.0f}%"

    print("## 1. 规模")
    if "unit_version" in key:
        print(f"- 测量单位：审阅单元（原始记录 {key['total_raw_changes']} 条）；单元内任一记录被标重点即推送。")
        print("- AI 自检在单元内命中任一原始记录即计该单元一次；不表示找全了该单元的全部风险。")
    print(f"- 原稿 {key['original_sentences']} 句，被改动 {key['changed_original_sentences']} 句"
          f"（{pct(key['changed_original_sentences'] / key['original_sentences'] if key['original_sentences'] else None)}）")
    print(f"- 全章修改 {key['total_changes']} 处：重点审阅 {g['flagged']['population']} 处"
          f"（{pct(g['flagged']['population'] / key['total_changes'])}），其余 {g['unflagged']['population']} 处")
    print(f"- 模式 {key['mode']}，标注样本 {len(gold)} 条")

    print("\n## 2. 高风险比例与规则筛选（由分层样本加权估计）")
    print("| 口径 | 重点审阅组 高风险/样本 | 其余组 高风险/样本 | 全章高风险比例 | 筛选召回率 | 重点审阅组精确率 |")
    print("|---|---|---|---|---|---|")
    for which, name in (("wide", "任一人认为需要看"), ("strict", "两人都认为需要看")):
        e = weighted_estimates(key, gold, which)
        print(f"| {name} | {e['flagged']['risky']}/{e['flagged']['sampled']} | "
              f"{e['unflagged']['risky']}/{e['unflagged']['sampled']} | {pct(e['high_risk_rate'])} | "
              f"{pct(e['flag_recall'])} | {pct(e['flag_precision'])} |")
    print("\n其余组里的高风险条目就是规则筛选漏掉的修改，逐条看一遍：")
    for cid, item in sorted(key["items"].items()):
        if not item["flagged"] and gold.get(cid, {}).get("wide"):
            print(f"- {cid}  原：{sheet_a[cid]['original']}  改：{sheet_a[cid]['revised']}")

    if args.selfcheck:
        print("\n## 3. AI 自检")
        runs = []
        for path, hits, unmatched in answer_runs:
            runs.append(hits)
            print(f"- {path.name}：点到 {len(hits)} 处修改；另有 {len(unmatched)} 条待人工核对，未计入命中")
        print("\n| 口径 | 样本内高风险条数 | " + " | ".join(f"第 {i + 1} 次找到" for i in range(len(runs)))
              + " | 规则筛选找到 |")
        print("|---|---|" + "---|" * (len(runs) + 1))
        for which, name in (("wide", "任一人认为需要看"), ("strict", "两人都认为需要看")):
            risky = [cid for cid in gold if gold[cid][which]]
            found = [str(sum(cid in hits for cid in risky)) for hits in runs]
            by_rules = sum(key["items"][cid]["flagged"] for cid in risky)
            print(f"| {name} | {len(risky)} | " + " | ".join(found) + f" | {by_rules} |")
        if len(runs) > 1:
            print("\n各次结果的重合度（交集 / 并集，在全章修改上算）：")
            for (i, x), (j, y) in combinations(enumerate(runs), 2):
                print(f"- 第 {i + 1} 次 与 第 {j + 1} 次：{jaccard(x, y):.2f}（交集 {len(x & y)}，并集 {len(x | y)}）")
        print("\n注意：样本按“重点审阅 / 其余”分层抽取，上表“找到”是样本内的原始条数，"
              "比较自检与规则筛选时两者用的是同一批样本。")


# ---------------------------------------------------------------- 命令行

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="试标与前置测量工具（Issue #3、#15）")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sheet", help="生成两份空白标注表和钥匙文件")
    p.add_argument("original", type=Path)
    p.add_argument("revised", type=Path)
    p.add_argument("--name", default="p01", help="文件名前缀，默认 p01")
    p.add_argument("--out-dir", type=Path, default=Path("data/labels"))
    p.add_argument("-n", type=int, default=30, help="抽多少条，默认 30")
    p.add_argument("--seed", type=int, default=20261110, help="随机种子，写进钥匙文件")
    p.add_argument("--mode", choices=("auto", "full", "rules_only"), default="auto")
    p.add_argument("--report", type=Path, help="复用已归档的原始审计报告，不重新审计")
    p.add_argument("--unit-map", type=Path, help="绑定该报告的完整单元分区 JSON；显式启用按单元抽样")
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("units", help="为已归档报告导出完整的自动归组候选，不含风险标签")
    p.add_argument("report", type=Path)
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--revised", type=Path, required=True)
    p.add_argument("--min-block", type=int, default=3)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_units)

    p = sub.add_parser("kappa", help="两份标注表的一致性")
    p.add_argument("sheet_a", type=Path)
    p.add_argument("sheet_b", type=Path)
    p.add_argument("--out", type=Path, help="把分歧清单写到这个 CSV")
    p.set_defaults(func=cmd_kappa)

    p = sub.add_parser("selfcheck", help="查看一份自检回答点到了哪些修改")
    p.add_argument("report", type=Path)
    p.add_argument("answers", type=Path, nargs="+")
    p.add_argument("--original", type=Path, required=True, help="与报告哈希一致的原稿全文")
    p.add_argument("--revised", type=Path, required=True, help="与报告哈希一致的改稿全文")
    p.add_argument("--mapping", type=Path, help="人工核对后的独立映射 CSV；不修改自检原始回答")
    p.add_argument("--pending-out", type=Path, help="把待人工核对的引用导出为新的映射 CSV 模板")
    p.set_defaults(func=cmd_selfcheck)

    p = sub.add_parser("measure", help="汇总 #15 的三个数字")
    p.add_argument("report", type=Path)
    p.add_argument("key", type=Path)
    p.add_argument("sheet_a", type=Path)
    p.add_argument("sheet_b", type=Path)
    p.add_argument("--selfcheck", type=Path, nargs="*", default=[], help="自检回答文件，可给多份")
    p.add_argument("--original", type=Path, required=True, help="与报告一致的原稿全文")
    p.add_argument("--revised", type=Path, required=True, help="与报告一致的改稿全文")
    p.add_argument("--mapping", type=Path, help="与 selfcheck 使用同一份人工映射 CSV")
    p.set_defaults(func=cmd_measure)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        ap.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()

