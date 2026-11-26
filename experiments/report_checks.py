"""实验入口的全文、报告完整性核对；不改变公开 schema 或历史报告。"""

from acceptance_desk import validate_report
from acceptance_desk._derived import checked_source


def check_source_report(report: dict, original: str, revised: str) -> None:
    try:
        # 导出核心会重走对齐，拒绝漏条、重复、错序和与当前对齐不符的旧报告。
        checked_source(report, original, revised)
        errors = validate_report(report, original, revised)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("原始审计报告结构损坏，请用对应工具与两份全文重新核对") from exc
    if errors:
        raise ValueError("原始审计报告未通过核对：" + "；".join(errors))
