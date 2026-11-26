"""Portable inference for a frozen rules-only logistic regression."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .feature_vector import RULES, vector


def _numeric(value: object) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _numbers(values: object) -> tuple[float, ...]:
    if not isinstance(values, list) or len(values) != len(RULES) or not all(map(_numeric, values)):
        raise ValueError("模型参数数目与规则特征不一致，或包含无效数值")
    return tuple(values)


@dataclass(frozen=True)
class RuleClassifier:
    lexicon_version: str
    threshold: float
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float

    @classmethod
    def from_json(cls, payload: object) -> RuleClassifier:
        if not isinstance(payload, dict) or payload.get("version") != "1" or payload.get("features") != RULES:
            raise ValueError("beta 模型格式或规则特征顺序不兼容")
        lexicon = payload.get("lexicon_version")
        threshold, intercept = payload.get("threshold"), payload.get("intercept")
        if not isinstance(lexicon, str) or not lexicon or not _numeric(threshold) or not 0 < threshold <= 1:
            raise ValueError("beta 模型缺少有效的词表版本或冻结阈值")
        if not _numeric(intercept):
            raise ValueError("beta 模型截距无效")
        scale = _numbers(payload.get("scale"))
        if any(value <= 0 for value in scale):
            raise ValueError("beta 模型的标准化尺度必须大于零")
        return cls(lexicon, threshold, _numbers(payload.get("mean")), scale, _numbers(payload.get("weights")), intercept)

    @classmethod
    def read(cls, path: Path) -> RuleClassifier:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def score(self, features: dict[str, float]) -> float:
        decision = self.intercept + math.fsum(
            (features[name] - mean) / scale * weight
            for name, mean, scale, weight in zip(RULES, self.mean, self.scale, self.weights, strict=True)
        )
        # Use the frozen estimator's sigmoid expression even for small negative logits.
        # The equivalent exp(x)/(1+exp(x)) rounds differently at the p04 boundary.
        try:
            return 1 / (1 + math.exp(-decision))
        except OverflowError:
            return 0.0

    def suggestions(self, report: dict) -> set[str]:
        if report["run"]["lexicon_version"] != self.lexicon_version:
            raise ValueError("审计词表版本与 beta 模型不一致")
        return {change["id"] for change in report["changes"]
                if self.score(vector(change, self.lexicon_version)) >= self.threshold}
