"""#19：普通 diff 基线、匿名交叉分配表、六人配对结果的严格汇总。

全部输出只写新文件；真实材料和观察记录放 data/private/，不进仓库。
"""

from __future__ import annotations

import argparse
import csv
from difflib import HtmlDiff
import hashlib
import html
import json
import math
from pathlib import Path
from statistics import mean, median

COLUMNS = ("participant_id", "period", "material_id", "tool", "seconds", "end_reason",
           "total_changes", "risky_found", "false_rejections", "probe_found", "other_opened", "pushed_accepted_risky")
LIMIT_SECONDS = 720
RISKY = 5


def baseline_html(original: str, revised: str, material_id: str) -> str:
    """全文左右 diff；不调用 audit，不读取模型、词表或参考标签，不加载远程资源。"""
    original = original.replace("\r\n", "\n").replace("\r", "\n")
    revised = revised.replace("\r\n", "\n").replace("\r", "\n")
    if not original.strip() or not revised.strip() or not material_id.strip():
        raise ValueError("材料编号、原稿和改稿必须非空")
    # make_file 的描述参数不会自行转义，文本内容由 HtmlDiff 负责转义。
    page = HtmlDiff(wrapcolumn=36).make_file(original.splitlines(), revised.splitlines(),
                                           fromdesc="原稿", todesc="改稿", context=False, charset="utf-8")
    title = f"普通 diff · {html.escape(material_id)}"
    page = page.replace("<title></title>", f"<title>{title}</title>")
    page = page.replace("</style>", "body {margin:24px; color:#17212d; background:#fff;} "
                        "table.diff {font-size:14px; line-height:1.65;} h1 {font-size:22px;} </style>", 1)
    intro = f"<h1>{title}</h1><p>找出你不愿意接受的修改。限时 12 分钟，可以提前结束。请向主持人指出对应行和文字。</p>"
    # 只存正文哈希，方便主持人核对与验收台所用版本一致；不携带文件路径或答案。
    hashes = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
              for name, text in (("original", original), ("revised", revised))}
    return page.replace("<body>", "<body>" + intro + "<!-- " + json.dumps(hashes) + " -->", 1)


def schedule() -> list[dict]:
    # 四个组合轮流：六人使组合频数为 2/2/1/1；工具先后各三人，工具×材料各三次。
    combinations = (("A", "diff"), ("A", "desk"), ("B", "diff"), ("B", "desk"))
    rows = []
    for i in range(6):
        material, tool = combinations[i % 4]
        for period in (1, 2):
            row = dict.fromkeys(COLUMNS, "")
            row.update(participant_id=f"S{i + 1:02d}", period=str(period), material_id=material, tool=tool)
            rows.append(row)
            material = "B" if material == "A" else "A"
            tool = "desk" if tool == "diff" else "diff"
    return rows


def _integer(value: str, name: str, minimum=0, maximum=None) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{name} 必须是非负整数")
    number = int(value)
    if number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"{name} 超出范围")
    return number


def summarize(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if len(reader.fieldnames or []) != len(COLUMNS) or set(reader.fieldnames or []) != set(COLUMNS):
            raise ValueError(f"记录表必须恰好包含规定的 {len(COLUMNS)} 列")
        raw = list(reader)
    expected = {(r["participant_id"], r["period"]): r for r in schedule()}
    if len(raw) != 12:
        raise ValueError("需要六人各两次的 12 条完整记录；不把未完成实验当作零值")
    seen, rows, material_totals = set(), [], {}
    for row in raw:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("记录表行列数不正确")
        row = {key: value.strip() for key, value in row.items()}
        key = (row["participant_id"], row["period"])
        if key not in expected or key in seen:
            raise ValueError("参与者/轮次为空、重复或不属于预定分配表")
        if any(row[k] != expected[key][k] for k in ("material_id", "tool")):
            raise ValueError("工具或材料与预定交叉分配不一致")
        seen.add(key)
        try:
            seconds = float(row["seconds"])
        except ValueError as exc:
            raise ValueError("seconds 必须是有效秒数") from exc
        if not math.isfinite(seconds) or not 0 < seconds <= LIMIT_SECONDS:
            raise ValueError("seconds 须大于 0 且不超过 720；超时统一记为 720")
        if row["end_reason"] not in ("submitted", "timeout") or (row["end_reason"] == "timeout" and seconds != LIMIT_SECONDS):
            raise ValueError("end_reason 须为 submitted / timeout，timeout 必须记 720 秒")
        total = _integer(row["total_changes"], "total_changes", RISKY)
        material = row["material_id"]
        if material in material_totals and material_totals[material] != total:
            raise ValueError("同一材料的修改总数不一致")
        material_totals[material] = total
        found = _integer(row["risky_found"], "risky_found", 0, RISKY)
        false_rejections = _integer(row["false_rejections"], "false_rejections", 0, total - RISKY)
        probe = _integer(row["probe_found"], "probe_found", 0, 1)
        if probe > found or found - probe > RISKY - 1:
            raise ValueError("探针发现数与高风险发现数不一致")
        opened = accepted = None
        if row["tool"] == "desk":
            opened = _integer(row["other_opened"], "other_opened", 0, 1)
            accepted = _integer(row["pushed_accepted_risky"], "pushed_accepted_risky", 0, RISKY - 1)
            if found - probe + accepted > RISKY - 1:
                raise ValueError("已推送高风险的发现数加接受数超过 4")
            if probe and not opened:
                raise ValueError("发现隐藏探针却未展开其余修改，请核对观察记录")
        elif row["other_opened"] or row["pushed_accepted_risky"]:
            raise ValueError("diff 没有推送分组，other_opened 和 pushed_accepted_risky 应留空")
        rows.append({"participant_id": key[0], "period": int(key[1]), "material_id": material,
                     "tool": row["tool"], "seconds": seconds, "end_reason": row["end_reason"],
                     "total_changes": total, "risky_found": found, "risky_total": RISKY,
                     "false_rejections": false_rejections, "probe_found": probe, "other_opened": opened,
                     "system_missed": 1 if row["tool"] == "desk" else None,
                     "user_missed_pushed": accepted,
                     "pushed_unresolved_risky": (RISKY - 1) - (found - probe) - accepted if row["tool"] == "desk" else None})
    paired = []
    for sid in sorted({row["participant_id"] for row in rows}):
        by_tool = {row["tool"]: row for row in rows if row["participant_id"] == sid}
        diff, desk = by_tool["diff"], by_tool["desk"]
        paired.append({"participant_id": sid, "diff_seconds": diff["seconds"], "desk_seconds": desk["seconds"],
                       "time_reduction_fraction": 1 - desk["seconds"] / diff["seconds"],
                       "diff_found": diff["risky_found"], "desk_found": desk["risky_found"],
                       "found_change": desk["risky_found"] - diff["risky_found"]})
    tools = {}
    for tool in ("diff", "desk"):
        selected = [r for r in rows if r["tool"] == tool]
        tools[tool] = {"n": len(selected), "mean_seconds": mean(r["seconds"] for r in selected),
                       "median_seconds": median(r["seconds"] for r in selected),
                       "found": sum(r["risky_found"] for r in selected), "risky_exposures": RISKY * len(selected),
                       "discovery_rate": sum(r["risky_found"] for r in selected) / (RISKY * len(selected)),
                       "false_rejections": sum(r["false_rejections"] for r in selected),
                       "timeouts": sum(r["end_reason"] == "timeout" for r in selected),
                       "probe_found": sum(r["probe_found"] for r in selected), "probe_exposures": len(selected)}
        if tool == "desk":
            tools[tool].update(other_opened_trials=sum(r["other_opened"] for r in selected),
                               pushed_accepted_risky=sum(r["user_missed_pushed"] for r in selected),
                               pushed_unresolved_risky=sum(r["pushed_unresolved_risky"] for r in selected))
    return {"study_version": "1", "participants": 6, "trials": sorted(rows, key=lambda r: (r["participant_id"], r["period"])),
            "paired": paired, "by_tool": tools,
            "mean_paired_time_reduction": mean(r["time_reduction_fraction"] for r in paired),
            "ratio_of_mean_time_reduction": 1 - tools["desk"]["mean_seconds"] / tools["diff"]["mean_seconds"],
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "note": "每篇预先确认 5 处高风险、其中 1 处隐藏探针。system_missed 是预置分组条件，"
                    "不是真实系统漏检率；发现数按人次累计。同一人两次配对，不视为 12 个独立被试；不自动撰写效果结论。"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="#19 用户实验准备与配对汇总")
    commands = parser.add_subparsers(dest="command", required=True)
    base = commands.add_parser("baseline")
    base.add_argument("original", type=Path)
    base.add_argument("revised", type=Path)
    base.add_argument("--material-id", required=True)
    base.add_argument("--out", type=Path, required=True)
    plan = commands.add_parser("schedule")
    plan.add_argument("--out", type=Path, required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("records", type=Path)
    summary.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schedule":
            with args.out.open("x", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(schedule())
        else:
            if args.command == "baseline":
                content = baseline_html(args.original.read_text(encoding="utf-8-sig"),
                                        args.revised.read_text(encoding="utf-8-sig"), args.material_id)
            else:
                content = json.dumps(summarize(args.records), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            with args.out.open("x", encoding="utf-8") as f:
                f.write(content)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
