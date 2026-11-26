"""Exercise the installed wheel outside the source checkout using public examples."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import acceptance_desk
import workbench
import review_ui
from acceptance_desk import validate_report
from acceptance_desk.lexicon import default_lexicon_path, load_lexicon
from acceptance_desk.scope_review import ReviewIntent, prepare_scope_review
from acceptance_desk.text import normalize


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    package = Path(acceptance_desk.__file__).resolve()
    if package.is_relative_to(source):
        raise RuntimeError(f"Expected an installed wheel, imported source checkout: {package}")
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"Package is outside the clean environment: {package}")
    assert default_lexicon_path().is_file()
    # 安装包里的词表必须与源码的词表逐字节相同（版本号随词表升级，不在这里写死）
    shipped = default_lexicon_path().read_bytes()
    assert shipped == (source / "acceptance_desk" / "resources" / "lexicon.toml").read_bytes()
    assert load_lexicon().version and load_lexicon().version in shipped.decode("utf-8")
    for module in (workbench, review_ui):
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert Path(workbench.__file__).with_name("style.css").is_file()
    from workbench.session import Session
    from workbench.app import EXAMPLE_SETS, EXAMPLES, import_view
    from nicegui import ui
    assert EXAMPLES.is_relative_to(Path(sys.prefix).resolve())
    for prefix, _ in EXAMPLE_SETS.values():
        for side in ("original", "revised"):
            name = f"{prefix}{side}.txt"
            assert (EXAMPLES / name).read_bytes() == (source / "examples" / name).read_bytes()
    with ui.column():
        import_view(Session({}), lambda *_: None)
    original = source / "examples/original.txt"
    revised = source / "examples/revised.txt"
    cli = Path(sys.executable).parent / "acceptance-desk"
    with tempfile.TemporaryDirectory(prefix="acceptance-wheel-") as tmp:
        report_path = Path(tmp) / "report.json"
        subprocess.run(
            [str(cli), str(original), str(revised), "--mode", "rules_only",
             "--out", str(report_path), "--brief"],
            cwd=tmp, check=True,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        errors = validate_report(
            report,
            normalize(original.read_text(encoding="utf-8")),
            normalize(revised.read_text(encoding="utf-8")),
        )
        assert not errors, errors
        assert report["run"]["mode"] == "rules_only"
        assert report["summary"]["total_changes"] > 0
        assert all(c["decision"]["status"] == "pending" for c in report["changes"])
        scope_request = prepare_scope_review(report, original.read_text(encoding="utf-8"),
                                             revised.read_text(encoding="utf-8"),
                                             [ReviewIntent("author-1", "保持论证的适用范围。")])
        assert scope_request.unit_ids and "以下为本轮审阅材料" in scope_request.prompt
        export_cli = Path(sys.executable).parent / "acceptance-desk-export"
        hints_path = Path(tmp) / "hints.json"
        subprocess.run([str(export_cli), "hints", str(report_path), str(original), str(revised),
                        "--out", str(hints_path)], cwd=tmp, check=True)
        assert json.loads(hints_path.read_text(encoding="utf-8"))["hints_version"] == "1"
        change = report["changes"][0]
        change["decision"] = {"status": "rejected", "final_text": change["original"]["text"],
                              "decided_by": "user", "decided_at": report["run"]["created_at"], "note": "公开示例演练"}
        reviewed_path = Path(tmp) / "reviewed.json"
        reviewed_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        feedback_path = Path(tmp) / "feedback.md"
        subprocess.run([str(export_cli), "feedback", str(reviewed_path), str(original), str(revised),
                        "--title", "公开虚构示例", "--scope", "local", "--select", change["id"],
                        "--out", str(feedback_path)], cwd=tmp, check=True)
        content = feedback_path.read_text(encoding="utf-8")
        assert change["original"]["text"] in content and "公开示例演练" in content
    print(f"Installed wheel verified: {package}")


if __name__ == "__main__":
    main()
