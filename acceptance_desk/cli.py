"""命令行入口：acceptance-desk 原稿.txt 改稿.txt [--mode auto|full|rules_only] [--out 报告.json] [--brief]"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from ._output import write_new_text
from .report import audit, validate_report
from .text import normalize


def _brief(report: dict) -> str:
    s, run = report["summary"], report["run"]
    lines = [
        f"模式 {run['mode']}｜修改 {s['total_changes']} 处｜重点审阅 {s['flagged']}｜其余 {s['unflagged']}｜"
        f"分类器 {run['classifier']['status']}，全部待人决定",
    ]
    for group, flag in (("重点审阅", True), ("其余修改", False)):
        rows = [c for c in report["changes"] if c["review"]["flagged"] == flag]
        if not rows:
            continue
        lines.append(f"\n== {group} ==")
        for c in rows:
            o, r = c["original"], c["revised"]
            lines.append(f"[{c['id']}] {c['alignment']}  原[{o['start']},{o['end']}) 改[{r['start']},{r['end']})")
            lines.append(f"  原：{o['text'] or '（无）'}")
            lines.append(f"  改：{r['text'] or '（无）'}")
            for reason in c["review"]["reasons"]:
                lines.append(f"  · [{reason['source']}] {reason['detail']}")
            nli = c["features"]["nli"]
            if nli:
                f, b = nli["forward"], nli["backward"]
                lines.append(f"  NLI 原→改 蕴含/中立/矛盾 {f['entailment']:.3f}/{f['neutral']:.3f}/{f['contradiction']:.3f}"
                             f"  改→原 {b['entailment']:.3f}/{b['neutral']:.3f}/{b['contradiction']:.3f}"
                             f"  相似度 {c['features']['similarity']:.3f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="acceptance-desk", description="检查改稿相对原稿的每处修改，输出 schema v0.1 报告")
    ap.add_argument("original", type=Path, help="原稿（UTF-8 纯文本，支持 BOM；段落之间空一行）")
    ap.add_argument("revised", type=Path, help="改稿")
    ap.add_argument("--mode", choices=("auto", "full", "rules_only"), default="auto")
    ap.add_argument("--model-cache", help="模型目录，默认 $ACCEPTANCE_DESK_MODEL_CACHE 或 ./.model-cache")
    ap.add_argument("--lexicon", help="自定义词表路径，默认用包内词表")
    ap.add_argument("--out", type=Path, help="把 JSON 报告写到尚不存在的新文件；不给则打印到标准输出")
    ap.add_argument("--brief", action="store_true", help="打印给人看的分组摘要（JSON 仍可用 --out 保存）")
    args = ap.parse_args(argv)

    try:
        # 与页面上传和意见导出一致：BOM 是文件编码标记，不属于正文。
        original = args.original.read_text(encoding="utf-8-sig")
        revised = args.revised.read_text(encoding="utf-8-sig")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            report = audit(original, revised, mode=args.mode, lexicon_path=args.lexicon, model_cache=args.model_cache)
    except (OSError, ValueError) as exc:
        ap.exit(2, f"[错误] {exc}\n")
    except RuntimeError as exc:  # mode="full" 而模型不可用
        sys.exit(f"[错误] {exc}")
    for w in caught:
        print(f"[提示] {w.message}", file=sys.stderr)

    errors = validate_report(report, normalize(original), normalize(revised))
    if errors:
        print("[错误] 报告自检未通过：\n  " + "\n  ".join(errors), file=sys.stderr)
        sys.exit(1)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        try:
            write_new_text(args.out, text + "\n")
        except OSError as exc:
            ap.exit(2, f"[错误] {exc}\n")
        print(f"[完成] 报告已写入 {args.out}（自检通过）", file=sys.stderr)
    if args.brief:
        print(_brief(report))
    elif not args.out:
        print(text)


if __name__ == "__main__":
    main()
