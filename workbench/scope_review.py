"""Run a scope review through the same BYOK connection used for revision."""

from collections.abc import Callable

from acceptance_desk.scope_review import ScopeReviewError, ScopeReviewRequest, ScopeReviewResult

from .model_connection import Connection


async def run_scope_review(connection: Connection, request: ScopeReviewRequest, *,
                           is_current: Callable[[], bool], timeout: int = 180) -> ScopeReviewResult:
    """A late result is discarded. Review does not write into a session or change decisions."""
    if not is_current():
        raise ScopeReviewError("作者要求或稿件已变化，请重新准备审阅。")
    if not request.unit_ids:
        return ScopeReviewResult(request.request_id, request.skill_version, (), (), request.unchecked_unit_ids)
    raw = await connection.generate(request.prompt, timeout=timeout)
    if not is_current():
        raise ScopeReviewError("审阅结束前稿件或作者要求已变化，旧结果已丢弃。")
    return request.parse_reply(raw)
