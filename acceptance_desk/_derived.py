"""派生只读结果共用的文稿、版本与修改位置检查。"""

from ._schema import SCHEMA_VERSION, numbered_id
from .decisions import ReviewDataError
from .export import _walk
from .text import normalize


def checked_source(report: dict, original: str, revised: str):
    if not isinstance(original, str) or not isinstance(revised, str):
        raise ReviewDataError("原稿和改稿必须是文本")
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise ReviewDataError("只支持 schema v0.1 的已审阅记录")
    run = report.get("run")
    if not isinstance(run, dict) or any(not isinstance(run.get(k), str) or not run[k].strip()
                                        for k in ("tool_version", "lexicon_version")):
        raise ReviewDataError("记录缺少工具或词表版本")
    original, revised = normalize(original), normalize(revised)
    # 重走现有合并校验，除了哈希和合法决定，还拒绝漏项、旧对齐和伪造的位置。
    segments, *_ = _walk(report, original, revised)
    changes = report["changes"]
    if any(c["id"] != numbered_id("c", k, len(changes)) for k, c in enumerate(changes, 1)):
        raise ReviewDataError("修改编号或顺序与当前审计格式不一致")
    positions = [(c["original"]["start"], c["original"]["end"], c["revised"]["start"], c["revised"]["end"])
                 for c in changes]
    if positions != sorted(positions):
        raise ReviewDataError("修改位置没有按正文顺序排列")
    return original, revised, segments
