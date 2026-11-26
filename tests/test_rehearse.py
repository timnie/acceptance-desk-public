"""完整链路使用虚构材料；fixture 与真实本地模型的验证分别记录。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.rehearse import run_rehearsal
from experiments.train import file_sha


def verify_run(path):
    run = json.loads((path / "rehearsal.json").read_text())
    assert run["not_effect_evidence"] is True and run["material_origin"] == "invented"
    for name, sha in run["files"].items():
        assert file_sha(path / name) == sha
    comparison = json.loads((path / "comparison.json").read_text())
    assert comparison["baseline"] == "rules_hit"
    assert set(comparison["schemes"]) == {"rules_hit", "rules", "models", "all"}
    for result in comparison["schemes"].values():
        assert result["coverage"]["id_set_matches"] and result["overall"]["n"] == 6
        assert result["overall"]["recall"]["confidence_interval"] is None
    for name in ("rules", "models", "all"):
        meta = path / f"model_{name}" / "metadata.json"
        assert file_sha(meta) == run["frozen_artifacts_unchanged"][name]["metadata.json"]
    return run


def test_full_fixture_workflow_through_cli(tmp_path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "rehearsal"
    result = subprocess.run([sys.executable, str(root / "experiments/rehearse.py"), "--out-dir", str(out)],
                            cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert verify_run(out)["model_features"] == "fixture"
    before = file_sha(out / "rehearsal.json")
    with pytest.raises(FileExistsError):
        run_rehearsal(out)
    assert file_sha(out / "rehearsal.json") == before


def test_full_workflow_with_actual_offline_models(tmp_path, models, model_cache):
    out = tmp_path / "actual-local-models"
    run_rehearsal(out, model_features="local", model_cache=model_cache)
    assert verify_run(out)["model_features"] == "local"
    report = json.loads((out / "test_report.json").read_text())
    assert report["run"]["models"] == models.info


def test_failed_rehearsal_removes_only_its_new_directory(tmp_path):
    out = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="模型不可用"):
        run_rehearsal(out, model_features="local", model_cache=str(tmp_path / "missing"))
    assert not out.exists()
