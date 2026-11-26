"""AI 改稿验收台核心包：对齐、特征、候选原因与 audit()。

接口格式见 docs/schema.md。
"""

__version__ = "0.0.1"

from .report import audit, validate_report  # noqa: E402  （report 需要先读到 __version__）
from .decisions import ReviewDataError, check_reviewed_report, decision_errors  # noqa: E402
from .export import (LayoutDiff, MergeError, MergeResult, PendingDecisions, document_segments,  # noqa: E402
                     layout_differences, merge_decisions, review_record_markdown)
from .hints import original_hints  # noqa: E402
from .feedback import build_feedback, feedback_markdown, feedback_text  # noqa: E402
from .evidence import change_evidence  # noqa: E402
from .units import review_units  # noqa: E402

__all__ = ["audit", "validate_report", "check_reviewed_report", "decision_errors", "ReviewDataError",
           "merge_decisions", "layout_differences", "document_segments", "review_record_markdown",
           "MergeResult", "LayoutDiff", "MergeError", "PendingDecisions", "__version__",
           "original_hints", "build_feedback", "feedback_markdown", "feedback_text", "change_evidence", "review_units"]
