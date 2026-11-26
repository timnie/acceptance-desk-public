"""共享夹具。

full 类用例需要本地模型：.model-cache/ 不存在（或未装 torch）时 skip；
设置 ACCEPTANCE_DESK_REQUIRE_MODELS=1 时改为失败，用来保证本机有模型时 full 用例确实运行。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from acceptance_desk.models import FeatureModels, ModelUnavailable, resolve_cache_dir

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def schema_example():
    """从 docs/schema.md §8 读出示例输入与期望输出，避免手抄出错。"""
    text = (ROOT / "docs" / "schema.md").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S)]
    inp = next(b for b in blocks if "original" in b and "run" not in b)
    exp = next(b for b in blocks if "run" in b)
    return inp, exp


@pytest.fixture(scope="session")
def model_cache() -> str:
    cache = os.environ.get("ACCEPTANCE_DESK_MODEL_CACHE") or str(ROOT / ".model-cache")
    return str(resolve_cache_dir(cache))


@pytest.fixture(scope="session")
def models(model_cache):
    try:
        return FeatureModels.load(model_cache)
    except ModelUnavailable as e:
        if os.environ.get("ACCEPTANCE_DESK_REQUIRE_MODELS") == "1":
            pytest.fail(f"要求运行 full 用例，但模型不可用：{e}")
        pytest.skip(f"本地模型不可用：{e}")


