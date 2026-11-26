"""标注员的一致性统计与显示格式。"""

from __future__ import annotations

from sklearn.metrics import cohen_kappa_score


def kappa(a: list, b: list) -> float | None:
    """Cohen's κ。两人都只用了同一个取值时 κ 没有定义，返回 None。"""
    if len(set(a) | set(b)) < 2:
        return None
    return float(cohen_kappa_score(a, b))


def format_kappa(k: float | None) -> str:
    return "无法计算" if k is None else f"{k:.2f}"
