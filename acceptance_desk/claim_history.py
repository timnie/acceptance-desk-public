"""Locate previously verified evidence in a later version without reusing a decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .align import align
from .scope_review import Evidence, ScopeReviewError
from .text import normalize, split_paragraphs


@dataclass(frozen=True)
class EvidenceLink:
    status: Literal["located", "changed_context", "ambiguous", "missing"]
    start: int | None = None
    end: int | None = None
    paragraph: int | None = None


def relink_evidence(previous: str, current: str, evidence: Evidence) -> EvidenceLink:
    """Return a location, or a location to inspect when the surrounding paragraph changed."""
    previous, current = normalize(previous), normalize(current)
    if (not 0 <= evidence.start < evidence.end <= len(previous)
            or previous[evidence.start:evidence.end] != evidence.quote):
        raise ScopeReviewError("上一轮证据与保存的稿件版本不一致。")
    old_spans = split_paragraphs(previous)
    previous_span = next(((start, end) for start, end in old_spans
                          if start <= evidence.start and evidence.end <= end), None)
    if previous_span is None:
        raise ScopeReviewError("上一轮证据缺少可定位的段落。")
    old_start, old_end = previous_span
    old_paragraph = previous[old_start:old_end]
    spans = split_paragraphs(current)
    if previous == current:
        return EvidenceLink("located", evidence.start, evidence.end, spans.index(previous_span) + 1)
    if sum(previous[start:end] == old_paragraph for start, end in old_spans) > 1:
        return EvidenceLink("ambiguous")

    paragraph_matches = [(i, start) for i, (start, end) in enumerate(spans, 1)
                         if current[start:end] == old_paragraph]
    if len(paragraph_matches) == 1:
        paragraph, start = paragraph_matches[0]
        start += evidence.start - old_start
        return EvidenceLink("located", start, start + len(evidence.quote), paragraph)
    if paragraph_matches:
        return EvidenceLink("ambiguous")

    matches = []
    for paragraph, (start, end) in enumerate(spans, 1):
        offset = current.find(evidence.quote, start, end)
        if offset == -1:
            continue
        if current.find(evidence.quote, offset + 1, end) != -1 or matches:
            return EvidenceLink("ambiguous")
        matches.append(EvidenceLink("changed_context", offset, offset + len(evidence.quote), paragraph))
    if matches:
        return matches[0]

    # Rewritten evidence has no exact quote in the next draft. Offer an aligned span for review.
    candidates = [
        pair.revised for pair in align(previous, current)
        if pair.original.start < evidence.end and evidence.start < pair.original.end and pair.revised.text
    ]
    if len(candidates) == 1:
        side = candidates[0]
        paragraph = side.paragraph + 1 if side.paragraph is not None else None
        return EvidenceLink("changed_context", side.start, side.end, paragraph)
    return EvidenceLink("ambiguous" if candidates else "missing")
