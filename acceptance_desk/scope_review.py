"""Prepare and validate an independent, read-only review of claim scope."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from typing import Literal

from ._schema import report_digest, text_sha256
from .text import normalize, split_paragraphs
from .units import review_units

SkillKind = Literal["widened", "narrowed", "preserved", "uncertain"]
Side = Literal["original", "revised"]
SKILL_VERSION = "scope-review/2"
MAX_SOURCE_CHARS = 100_000
SUPPORT_BY_KIND = {
    "widened": frozenset({"sufficient", "insufficient", "uncertain"}),
    "narrowed": frozenset({"sufficient", "insufficient", "uncertain"}),
    "preserved": frozenset({"not_applicable"}),
    "uncertain": frozenset({"uncertain"}),
}


class ScopeReviewError(ValueError):
    """The input or model response is unsuitable for this review."""


@dataclass(frozen=True)
class ReviewIntent:
    id: str
    text: str


@dataclass(frozen=True)
class Evidence:
    side: Side
    paragraph: str
    start: int
    end: int
    quote: str
    role: str


@dataclass(frozen=True)
class Finding:
    unit_id: str
    change_ids: tuple[str, ...]
    kind: SkillKind
    support: str
    evidence: tuple[Evidence, ...]
    intent_ids: tuple[str, ...]
    effect: str
    question: str
    missing: str


@dataclass(frozen=True)
class ReviewIssue:
    unit_id: str | None
    code: str
    detail: str


@dataclass(frozen=True)
class ScopeReviewResult:
    request_id: str
    skill_version: str
    findings: tuple[Finding, ...]
    issues: tuple[ReviewIssue, ...]
    unchecked_unit_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Paragraph:
    id: str
    side: Side
    start: int
    text: str

    def find_quote(self, quote: str) -> int:
        offset = self.text.find(quote)
        if offset < 0:
            raise ScopeReviewError("引文不在指定段落中。")
        if self.text.find(quote, offset + 1) != -1:
            raise ScopeReviewError("引文在指定段落内重复，需要更具体的摘录。")
        return self.start + offset


@dataclass(frozen=True)
class _ChangeSide:
    start: int
    end: int
    paragraph: str | None
    text: str


@dataclass(frozen=True)
class _Change:
    id: str
    original: _ChangeSide
    revised: _ChangeSide

    def overlaps(self, anchor: Evidence) -> bool:
        side = self.original if anchor.side == "original" else self.revised
        return side.start < side.end and side.start < anchor.end and anchor.start < side.end


@dataclass(frozen=True)
class _Unit:
    id: str
    changes: tuple[_Change, ...]


@dataclass(frozen=True)
class ScopeReviewRequest:
    request_id: str
    skill_version: str
    original_sha256: str
    revised_sha256: str
    intent_sha256: str
    intents: tuple[ReviewIntent, ...] = field(repr=False)
    _paragraphs: tuple[_Paragraph, ...] = field(repr=False)
    _units: tuple[_Unit, ...] = field(repr=False)
    unchecked_unit_ids: tuple[str, ...]
    _skill: str = field(repr=False)

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.id for unit in self._units)

    @property
    def change_ids_by_unit(self) -> dict[str, tuple[str, ...]]:
        return {unit.id: tuple(change.id for change in unit.changes) for unit in self._units}

    def matches(self, original: str, revised: str, intents: Sequence[ReviewIntent]) -> bool:
        return (self.original_sha256 == text_sha256(normalize(original))
                and self.revised_sha256 == text_sha256(normalize(revised))
                and self.intent_sha256 == _intent_digest(tuple(intents)))

    @property
    def payload(self) -> dict:
        return {
            "request_id": self.request_id,
            "skill_version": self.skill_version,
            "author_intents": [asdict(intent) for intent in self.intents],
            "original": [{"id": p.id, "text": p.text} for p in self._paragraphs if p.side == "original"],
            "revised": [{"id": p.id, "text": p.text} for p in self._paragraphs if p.side == "revised"],
            "units": [{"unit_id": unit.id, "changes": [asdict(change) for change in unit.changes]}
                      for unit in self._units],
        }

    @property
    def prompt(self) -> str:
        return self._skill + "\n\n以下为本轮审阅材料。\n" + json.dumps(self.payload, ensure_ascii=False)

    def parse_reply(self, raw: str) -> ScopeReviewResult:
        response = _response_object(raw)
        if response.get("request_id") != self.request_id or response.get("skill_version") != self.skill_version:
            raise ScopeReviewError("模型回复对应其他稿件、作者要求或审阅 Skill 版本。")
        items = response.get("findings")
        if not isinstance(items, list):
            raise ScopeReviewError("模型回复缺少 findings 数组。")

        known = set(self.unit_ids)
        received = {}
        repetitions = Counter()
        issues = []
        for item in items:
            unit_id = item.get("unit_id") if isinstance(item, dict) else None
            if not isinstance(unit_id, str) or unit_id not in known:
                issues.append(ReviewIssue(None, "unknown_unit", "模型回复包含本轮未发送的审阅单元。"))
                continue
            received[unit_id] = item
            repetitions[unit_id] += 1

        paragraphs = {p.id: p for p in self._paragraphs}
        intent_ids = {intent.id for intent in self.intents}
        findings = []
        for unit in self._units:
            if not repetitions[unit.id]:
                issues.append(ReviewIssue(unit.id, "missing_unit", "模型尚未返回这处改动的审阅结果。"))
            elif repetitions[unit.id] > 1:
                issues.append(ReviewIssue(unit.id, "duplicate_unit", "同一审阅单元收到多条相互竞争的结果。"))
            else:
                try:
                    findings.append(_parse_finding(unit, received[unit.id], paragraphs, intent_ids))
                except ScopeReviewError as exc:
                    issues.append(ReviewIssue(unit.id, "invalid_finding", str(exc)))

        return ScopeReviewResult(self.request_id, self.skill_version, tuple(findings), tuple(issues),
                                 self.unchecked_unit_ids)


def _intent_digest(intents: tuple[ReviewIntent, ...]) -> str:
    return report_digest({"author_intents": [asdict(intent) for intent in intents]})


def _paragraphs(text: str, side: Side, prefix: str) -> tuple[_Paragraph, ...]:
    return tuple(_Paragraph(f"{prefix}{number}", side, start, text[start:end])
                 for number, (start, end) in enumerate(split_paragraphs(text), 1))


def _change_side(span: dict, prefix: str) -> _ChangeSide:
    paragraph = span["paragraph"]
    return _ChangeSide(span["start"], span["end"], f"{prefix}{paragraph + 1}" if paragraph is not None else None,
                       span["text"])


def prepare_scope_review(report: dict, original: str, revised: str, intents: Sequence[ReviewIntent] = (),
                         *, unit_ids: Sequence[str] | None = None) -> ScopeReviewRequest:
    if not isinstance(original, str) or not isinstance(revised, str):
        raise ScopeReviewError("原稿和改稿必须是文本。")
    original, revised = normalize(original), normalize(revised)
    if len(original) + len(revised) > MAX_SOURCE_CHARS:
        raise ScopeReviewError("两版全文超出本次审阅上限，请选择较短的章节后再审阅。")
    intents = tuple(intents)
    if any(not isinstance(intent, ReviewIntent) or not isinstance(intent.id, str) or not intent.id.strip()
           or not isinstance(intent.text, str) or not intent.text.strip() for intent in intents):
        raise ScopeReviewError("仅发送带编号、内容非空且已由作者确认的要求。")
    if len({intent.id for intent in intents}) != len(intents):
        raise ScopeReviewError("作者要求编号重复。")

    groups = review_units(report, original, revised)
    known = {unit["id"] for unit in groups}
    if unit_ids is not None:
        if any(not isinstance(unit_id, str) or unit_id not in known for unit_id in unit_ids):
            raise ScopeReviewError("指定的审阅单元不属于这对文稿。")
        if len(set(unit_ids)) != len(unit_ids):
            raise ScopeReviewError("审阅单元编号重复。")
    selected = known if unit_ids is None else set(unit_ids)
    changes = {
        c["id"]: _Change(c["id"], _change_side(c["original"], "o"), _change_side(c["revised"], "r"))
        for c in report["changes"]
    }
    units = tuple(_Unit(group["id"], tuple(changes[cid] for cid in group["change_ids"]))
                  for group in groups if group["id"] in selected)
    unchecked = tuple(group["id"] for group in groups if group["id"] not in selected)
    original_hash, revised_hash, intent_hash = text_sha256(original), text_sha256(revised), _intent_digest(intents)
    skill = files("acceptance_desk").joinpath("resources", "scope_review.md").read_text(encoding="utf-8")
    request_id = report_digest({
        "original": original_hash, "revised": revised_hash, "intents": intent_hash,
        "skill_version": SKILL_VERSION, "skill_sha256": text_sha256(skill),
        "units": [asdict(unit) for unit in units], "unchecked": unchecked,
    })
    return ScopeReviewRequest(request_id, SKILL_VERSION, original_hash, revised_hash, intent_hash, intents,
                              _paragraphs(original, "original", "o") + _paragraphs(revised, "revised", "r"),
                              units, unchecked, skill)


def _response_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith(("\x60\x60\x60json\n", "\x60\x60\x60\n")) and raw.endswith("\n\x60\x60\x60"):
        raw = raw.split("\n", 1)[1].rsplit("\n", 1)[0]
    try:
        response = json.loads(raw.removesuffix(";"))
    except ValueError as exc:
        raise ScopeReviewError("模型未返回审阅 JSON 对象。") from exc
    if not isinstance(response, dict):
        raise ScopeReviewError("模型未返回审阅 JSON 对象。")
    return response


def _anchors(raw: object, paragraphs: dict[str, _Paragraph]) -> tuple[Evidence, ...]:
    if not isinstance(raw, list):
        raise ScopeReviewError("模型回复缺少 evidence 数组。")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise ScopeReviewError("引文必须包含段号、逐字摘录和用途。")
        pid, quote, role = item.get("paragraph"), item.get("quote"), item.get("role")
        if (not isinstance(pid, str) or pid not in paragraphs or not isinstance(quote, str)
                or not quote.strip() or role not in ("claim", "limitation", "support")):
            raise ScopeReviewError("引文必须包含有效段号、逐字摘录和用途。")
        paragraph = paragraphs[pid]
        start = paragraph.find_quote(quote)
        result.append(Evidence(paragraph.side, pid, start, start + len(quote), quote, role))
    if len(set(result)) != len(result):
        raise ScopeReviewError("同一条审阅建议重复列出了同一引文。")
    return tuple(result)


def _parse_finding(unit: _Unit, item: dict, paragraphs: dict[str, _Paragraph], known_intents: set[str]) -> Finding:
    kind, support = item.get("kind"), item.get("support")
    if not isinstance(kind, str) or not isinstance(support, str) or support not in SUPPORT_BY_KIND.get(kind, ()):
        raise ScopeReviewError("范围判断与证据状态不匹配。")
    effect, question, missing = (item.get(key) for key in ("effect", "question", "missing"))
    if any(not isinstance(text, str) for text in (effect, question, missing)):
        raise ScopeReviewError("模型回复缺少范围影响、核对问题或缺少材料字段。")
    if kind == "preserved" and (question.strip() or missing.strip()):
        raise ScopeReviewError("若仍有问题待核对，不能把本处标为范围未变。")
    if kind != "preserved" and (not effect.strip() or not question.strip()):
        raise ScopeReviewError("模型需要说明对主张的影响，并给作者一个具体问题。")
    if support in ("insufficient", "uncertain") and not missing.strip():
        raise ScopeReviewError("请说明缺少哪项材料或需要澄清什么。")

    intents = item.get("intent_ids")
    if (not isinstance(intents, list) or any(not isinstance(x, str) or x not in known_intents for x in intents)
            or len(set(intents)) != len(intents)):
        raise ScopeReviewError("模型引用的作者要求不存在或重复。")
    evidence = _anchors(item.get("evidence"), paragraphs)
    if support == "sufficient" and not any(anchor.side == "revised" and anchor.role == "support"
                                           for anchor in evidence):
        raise ScopeReviewError("标为稿内依据充分时，须引用改稿中可定位的支撑材料。")
    if kind in ("widened", "narrowed"):
        sides = {anchor.side for anchor in evidence if anchor.role == "claim"}
        if sides != {"original", "revised"}:
            raise ScopeReviewError("范围变化需要原稿及改稿两侧的可定位结论。")
        if not any(change.overlaps(anchor) for change in unit.changes for anchor in evidence
                   if anchor.role != "support"):
            raise ScopeReviewError("所引结论或限定与这个审阅单元没有重叠。")
    return Finding(unit.id, tuple(change.id for change in unit.changes), kind, support, evidence, tuple(intents),
                   effect.strip(), question.strip(), missing.strip())
