"""审计报告、审阅记录与冻结特征向量共用的格式约定。"""

import hashlib
import json

SCHEMA_VERSION = "0.1"
# 此顺序也用于冻结分类器的特征列。
ALIGNMENTS = ("1-1", "1-2", "2-1", "0-1", "1-0")


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def report_digest(report: dict) -> str:
    """固定 JSON 的键顺序与编码，以便识别同一份报告或审阅记录。"""
    return text_sha256(json.dumps(report, ensure_ascii=False, sort_keys=True))


def is_integer(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def numbered_id(prefix: str, index: int, count: int) -> str:
    """总数达到千条时，整份清单统一采用至少四位编号。"""
    width = 3 if count < 1000 else 4
    return f"{prefix}{index:0{width}d}"
