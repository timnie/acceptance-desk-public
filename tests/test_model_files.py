"""回归：模型文件核对（PR #14 审阅 4）。只用小临时文件和替身，不加载真实模型。"""

import hashlib

import pytest

from acceptance_desk import audit
from acceptance_desk import models as m


def make_cache(tmp_path, *, nli_meta=None, weight=b"tiny"):
    cache = tmp_path / "cache"
    for rel in m.REQUIRED_FILES:
        p = cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(weight if rel.endswith((".bin", ".safetensors")) else b"{}")
    if nli_meta is not None:
        meta = cache / m.NLI_DIR / ".cache" / "huggingface" / "download" / "pytorch_model.bin.metadata"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(nli_meta, encoding="utf-8")
    return cache


@pytest.mark.parametrize("content", ["", "\n", "   \n"])
def test_empty_metadata_is_unavailable(tmp_path, content):
    cache = make_cache(tmp_path, nli_meta=content)
    with pytest.raises(m.ModelUnavailable, match="为空或格式不对"):
        m.verify_files(cache)


def test_wrong_revision_metadata(tmp_path):
    cache = make_cache(tmp_path, nli_meta="0" * 40 + "\n")
    with pytest.raises(m.ModelUnavailable, match="期望 " + m.NLI_REVISION):
        m.verify_files(cache)


def test_corrupted_weights_fail_sha256(tmp_path):
    cache = make_cache(tmp_path, nli_meta=m.NLI_REVISION + "\nxx\n")
    with pytest.raises(m.ModelUnavailable, match="SHA256 不符"):
        m.verify_files(cache)


def test_verify_passes_with_matching_hashes(tmp_path, monkeypatch):
    cache = make_cache(tmp_path, nli_meta=m.NLI_REVISION + "\n")
    digest = hashlib.sha256(b"tiny").hexdigest()
    monkeypatch.setattr(m, "EXPECTED_SHA256", {k: digest for k in m.EXPECTED_SHA256})
    m.verify_files(cache)  # 不抛异常


@pytest.mark.parametrize("meta", ["", None])
def test_auto_falls_back_and_full_reports(tmp_path, meta):
    """空 metadata（审阅复现）和损坏权重两种情况：auto 退回 rules_only，full 报清楚的错误。"""
    cache = str(make_cache(tmp_path, nli_meta=meta))
    with pytest.warns(RuntimeWarning, match="退回 rules_only"):
        rep = audit("问卷可能有效。", "问卷有效。", model_cache=cache)
    assert rep["run"]["mode"] == "rules_only"
    with pytest.raises(RuntimeError, match="为空或格式不对" if meta == "" else "SHA256 不符"):
        audit("问卷可能有效。", "问卷有效。", mode="full", model_cache=cache)

