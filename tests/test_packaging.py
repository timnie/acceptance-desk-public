"""词表作为包资源（A22 的包内部分；wheel 与 Python 3.10 的核对见 PR 说明中的命令）。"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from acceptance_desk.lexicon import CATEGORIES, default_lexicon_path, load_lexicon

ROOT = Path(__file__).resolve().parents[1]


def test_lexicon_is_package_resource():
    assert default_lexicon_path().is_file()
    lex = load_lexicon()
    assert lex.version == "0.2"
    assert {c for c, _, _ in lex.entries} == {*CATEGORIES, "ignore"}


@pytest.mark.skipif(shutil.which("uv") is None, reason="需要 uv 构建 wheel")
def test_wheel_contains_lexicon(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
                   check=True, capture_output=True)
    (whl,) = tmp_path.glob("*.whl")
    names = zipfile.ZipFile(whl).namelist()
    assert "acceptance_desk/resources/lexicon.toml" in names
    assert "acceptance_desk/resources/scope_review.md" in names
    assert "acceptance_desk/report.py" in names
    assert "acceptance_desk/hints.py" in names
    assert "acceptance_desk/feedback.py" in names
    assert "acceptance_desk/feedback_cli.py" in names
    assert "workbench/__main__.py" in names
    assert "workbench/style.css" in names
    for stem in ("original", "revised", "long_original", "long_revised"):
        assert f"workbench/examples/{stem}.txt" in names
    assert "review_ui/state.py" in names
    assert "review_ui/render.py" in names
    assert "mcp_server.py" in names
