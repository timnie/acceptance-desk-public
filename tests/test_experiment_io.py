"""实验结果的失败恢复：仅清理本批新文件，保护已有结果及其链接。"""

from pathlib import Path

import pytest

from experiments.experiment_io import write_new_files


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink"])
def test_late_conflict_rolls_back_earlier_files(tmp_path, kind):
    existing, first, last = [tmp_path / name for name in ("existing", "first.json", "last.csv")]
    existing.write_text("keep")
    if kind == "symlink": last.symlink_to(existing)
    elif kind == "hardlink": last.hardlink_to(existing)
    else: last.write_text("keep")
    with pytest.raises(FileExistsError):
        write_new_files([(first, "new"), (last, "must not write")])
    assert not first.exists() and existing.read_text() == last.read_text() == "keep"


def test_same_output_path_is_rejected_before_creating_it(tmp_path):
    path = tmp_path / "same.json"
    with pytest.raises(ValueError, match="重复"):
        write_new_files([(path, "first"), (tmp_path / "." / "same.json", "second")])
    assert not path.exists()


def test_interrupted_write_removes_all_new_files(tmp_path, monkeypatch):
    original_open = Path.open
    class FailingWriter:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def write(self, text):
            self.file.write(text[:3])
            raise OSError("simulated disk full")
    def open_with_failure(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        return FailingWriter(file) if path.name == "last.json" else file
    monkeypatch.setattr(Path, "open", open_with_failure)
    with pytest.raises(OSError, match="disk full"):
        write_new_files([(tmp_path / "first.json", "first"), (tmp_path / "last.json", "partial")])
    assert list(tmp_path.iterdir()) == []
