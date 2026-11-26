"""命令行审计保护输入，并与后续本地导出使用相同的 UTF-8 解码方式。"""

import json
import subprocess
import sys

import pytest

from acceptance_desk import cli, validate_report
from acceptance_desk.feedback_cli import main as export_main
from acceptance_desk.text import normalize


@pytest.fixture
def sources(tmp_path):
    original, revised = tmp_path / "original.txt", tmp_path / "revised.txt"
    original.write_text("研究可能支持这一结论。\n", encoding="utf-8")
    revised.write_text("研究支持这一结论。\n", encoding="utf-8")
    return original, revised


def arguments(sources):
    return [*(str(p) for p in sources), "--mode", "rules_only"]


@pytest.mark.parametrize("target", ["original", "revised", "existing", "symlink", "hardlink", "dangling"])
def test_output_never_overwrites_existing_files(sources, tmp_path, target, capsys):
    original, revised = sources
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"existing results\n")
    snapshots = {p: p.read_bytes() for p in (*sources, existing)}
    out = tmp_path / "out.json"
    if target == "original":
        out = original
    elif target == "revised":
        out = revised
    elif target == "existing":
        out = existing
    elif target == "symlink":
        out.symlink_to(original)
    elif target == "hardlink":
        out.hardlink_to(original)
    else:
        out.symlink_to(tmp_path / "absent.txt")
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(sources) + ["--out", str(out)])
    assert exc.value.code == 2
    assert all(p.read_bytes() == before for p, before in snapshots.items())
    assert not (tmp_path / "absent.txt").exists()
    captured = capsys.readouterr()
    assert not captured.out and "[错误]" in captured.err


def test_output_created_during_audit_is_also_preserved(sources, tmp_path, monkeypatch):
    out = tmp_path / "report.json"
    validate = cli.validate_report

    def concurrent_create(*args):
        out.write_bytes(b"another process saved this")
        return validate(*args)

    monkeypatch.setattr(cli, "validate_report", concurrent_create)
    with pytest.raises(SystemExit):
        cli.main(arguments(sources) + ["--out", str(out)])
    assert out.read_bytes() == b"another process saved this"


def test_bom_crlf_report_round_trips_through_export(sources, tmp_path, capsys):
    original, revised = sources
    for path in sources:
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    snapshots = {p: p.read_bytes() for p in sources}
    out = tmp_path / "report.json"
    cli.main(arguments(sources) + ["--out", str(out), "--brief"])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert validate_report(report, *(normalize(p.read_text(encoding="utf-8-sig")) for p in sources)) == []
    assert report["summary"]["total_changes"] == 1
    assert "\ufeff" not in report["changes"][0]["original"]["text"]
    hints = tmp_path / "hints.json"
    export_main(["hints", str(out), str(original), str(revised), "--out", str(hints)])
    assert json.loads(hints.read_text(encoding="utf-8"))["hints_version"] == "1"
    captured = capsys.readouterr()
    assert "修改 1 处" in captured.out and "[完成]" in captured.err
    assert all(p.read_bytes() == before for p, before in snapshots.items())


def test_module_stdout_is_json(sources):
    result = subprocess.run([sys.executable, "-m", "acceptance_desk", *arguments(sources)],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["run"]["mode"] == "rules_only"
    assert report["summary"]["total_changes"] == 1
    assert not result.stderr


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("problem", ["missing", "directory", "encoding"])
def test_bad_inputs_exit_cleanly_without_output(sources, tmp_path, side, problem, capsys):
    path = sources[side]
    if problem == "encoding":
        path.write_bytes(b"\xff\xfe")
    else:
        path.unlink()
        if problem == "directory":
            path.mkdir()
    out = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(sources) + ["--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    captured = capsys.readouterr()
    assert not captured.out and "[错误]" in captured.err


def test_unwritable_output_exits_cleanly(sources, tmp_path, capsys):
    out = tmp_path / "missing" / "report.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(sources) + ["--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    assert "[错误]" in capsys.readouterr().err


def test_missing_lexicon_exits_cleanly(sources, tmp_path, capsys):
    out = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments(sources) + ["--lexicon", str(tmp_path / "missing.toml"), "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    assert "[错误]" in capsys.readouterr().err
