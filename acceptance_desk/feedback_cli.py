"""本地导出派生提示和作者意见；不联网，只写入新文件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ._output import write_new_text
from .feedback import SCOPES, build_feedback, feedback_markdown, feedback_text
from .hints import original_hints


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="acceptance-desk-export", description="本地导出原稿已有提示和作者修改意见")
    sub = parser.add_subparsers(dest="command", required=True)
    hints = sub.add_parser("hints", help="独立的原稿保留词表命中 JSON")
    feedback = sub.add_parser("feedback", help="由人工决定生成 Markdown / TXT / JSON 意见")
    for command in (hints, feedback):
        command.add_argument("report", type=Path, help="已审阅记录 JSON，也可用原始审计报告")
        command.add_argument("original", type=Path)
        command.add_argument("revised", type=Path)
        command.add_argument("--out", type=Path, required=True, help="必须是尚不存在的新文件")
    hints.add_argument("--lexicon", help="版本必须与报告匹配；默认使用包内词表")
    feedback.add_argument("--title", required=True)
    feedback.add_argument("--scope", choices=SCOPES, required=True, help="作者明确选择：local 局部修订 / rewrite 整体改写")
    feedback.add_argument("--format", choices=("markdown", "txt", "json"), default="markdown")
    feedback.add_argument("--select", nargs="+", metavar="CHANGE_ID", help="只导出这些修改，仍按正文顺序组织")
    feedback.add_argument("--include-pending", action="store_true")
    feedback.add_argument("--without-context", action="store_true")
    feedback.add_argument("--now", help="可选的带时区 ISO 8601 时间，用于复现导出")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        original = args.original.read_text(encoding="utf-8-sig")
        revised = args.revised.read_text(encoding="utf-8-sig")
        if args.command == "hints":
            result = {"hints_version": "1", "source": report.get("document") if isinstance(report, dict) else None,
                      "hints": original_hints(report, original, revised, lexicon_path=args.lexicon)}
            content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        else:
            options = dict(title=args.title, scope=args.scope, selected_ids=args.select,
                           include_pending=args.include_pending, include_context=not args.without_context, now=args.now)
            if args.format == "json":
                content = json.dumps(build_feedback(report, original, revised, **options), ensure_ascii=False,
                                     indent=2, allow_nan=False) + "\n"
            else:
                render = feedback_markdown if args.format == "markdown" else feedback_text
                content = render(report, original, revised, **options)
        write_new_text(args.out, content)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
