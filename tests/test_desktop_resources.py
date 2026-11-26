"""The opt-in desktop experiment must reject missing or altered model resources."""
from hashlib import sha256

import pytest

from desktop import resources


def test_check_frozen_model_before_opening_workbench(tmp_path, monkeypatch):
    model = tmp_path / "run03.json"
    monkeypatch.setattr(resources, "MODEL_SHA256", sha256(b"fixture").hexdigest())

    with pytest.raises(ValueError, match="找不到"):
        resources.verify_model(model)
    model.write_bytes(b"fixture")
    assert resources.verify_model(model) == model
    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="校验失败"):
        resources.verify_model(model)
