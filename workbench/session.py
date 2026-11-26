"""工作台状态与本机存储，不依赖 NiceGUI，失败的恢复不改动当前决定或旧存档。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from collections.abc import MutableMapping

from acceptance_desk import document_segments
from acceptance_desk.rule_classifier import RuleClassifier
from review_ui import state

from .radar_priority import reference_evidence
from .view import DECISION_ZH, number

STORE_KEY = "workbench"
MODEL_FILTER = "model_suggested"


class Session:
    def __init__(self, storage: MutableMapping | None = None, *, beta_model: RuleClassifier | None = None) -> None:
        self.beta_model = beta_model
        self.rounds: list[dict] = []
        self.model_suggestions: set[str] | None = None
        self.model_error: str | None = None
        self.reference_suggestions: dict[str, tuple[tuple[str, str], ...]] = {}
        self.storage = storage if storage is not None else {}
        self.save_enabled = True
        self.original = self.revised = ""
        self.title = "文稿"
        self.names: dict = {}
        self.formats = {"original": "txt", "revised": "txt"}
        self.report: dict | None = None
        self.review: dict | None = None
        self.segments: list[dict] = []
        self.undo: list[dict] = []
        self.redo: list[dict] = []
        self.drafts: dict[str, str] = {}
        self.search = ""
        self.notes_only = False
        self.saved_revision: str | None = None
        self.cur: str | None = None
        self.flt = "all"
        self.auto_advance = True
        self.display = "proof"
        self.unpaired_rewrite = False

    @property
    def by_id(self) -> dict:
        return {c["id"]: c for c in self.review["changes"]}

    @property
    def order(self) -> list[str]:
        return [c["id"] for c in self.review["changes"]]

    def visible(self, filter_name: str | None = None) -> list[str]:
        name = filter_name or self.flt
        if name == MODEL_FILTER:
            priority = (self.reference_suggestions
                        if self.reference_suggestions or self.radar_saturated else self.model_suggestions)
            ids = [cid for cid in self.order if cid in (priority or ())]
        else:
            ids = state.visible_ids(self.review, name)
        query = self.search.strip().casefold()
        changes = self.by_id
        return [cid for cid in ids
                if (not self.notes_only or changes[cid]["decision"]["note"].strip())
                and (not query or query in "\n".join([
                    changes[cid]["original"]["text"], changes[cid]["revised"]["text"],
                    changes[cid]["decision"]["final_text"] or "", changes[cid]["decision"]["note"],
                ]).casefold())]

    @property
    def radar_saturated(self) -> bool:
        return bool(self.model_suggestions) and len(self.model_suggestions) == len(self.review["changes"])

    def _reset_navigation(self) -> None:
        self.undo = []
        self.redo = []
        self.drafts = {}
        self.search = ""
        self.notes_only = False
        self.flt = "all"
        vis = self.visible()
        pending = state.batch_targets(self.review, vis)
        self.cur = next(iter(pending or vis), None)

    def load(self, original: str, revised: str, report: dict, *, title: str, names: dict, formats: dict,
             saved_review: dict | str | bytes | None = None) -> str | None:
        review = state.new_review(report)
        if saved_review is not None:
            source = json.dumps(saved_review, ensure_ascii=False) if isinstance(saved_review, dict) else saved_review
            review = state.load_review(source, report)
        segments = document_segments(review, original, revised)
        suggestions = model_error = None
        if self.beta_model is not None:
            try:
                suggestions = self.beta_model.suggestions(report)
            except (ValueError, ArithmeticError) as exc:
                model_error = str(exc)
        # 完整校验后才提交状态；坏文件不会清空决定、撤销或旧存档。
        self.model_suggestions, self.model_error = suggestions, model_error
        self.original, self.revised, self.report = original, revised, report
        self.title, self.names, self.formats = title, dict(names), dict(formats)
        self.review, self.segments = review, segments
        self.reference_suggestions = (
            reference_evidence(original, revised, review["changes"])
            if suggestions is None or self.radar_saturated else {}
        )
        if self.radar_saturated and len(self.reference_suggestions) == len(review["changes"]):
            self.reference_suggestions.clear()
        alignments = {change["alignment"] for change in review["changes"]}
        self.unpaired_rewrite = alignments == {"0-1", "1-0"} and all(
            segment["change_id"] is not None or not segment["original"].strip()
            for segment in segments
        )
        if self.unpaired_rewrite:
            self.display = "drafts"
        elif self.display == "drafts":
            self.display = "proof"
        self._reset_navigation()
        return self.restored_message() if saved_review is not None else None

    def restore(self, source: str | bytes) -> str:
        review = state.load_review(source, self.report)
        segments = document_segments(review, self.original, self.revised)
        self.review, self.segments = review, segments
        drafts = self.drafts
        self._reset_navigation()
        changes = self.by_id
        self.drafts = {cid: text for cid, text in drafts.items() if cid in changes}
        return self.restored_message()

    def restored_message(self) -> str:
        p = state.progress(self.review)
        return f"已恢复进度：{p['decided']} / {p['total']} 处已决定。撤销记录不保留。"

    def record(self, ids: list[str], label: str) -> None:
        state.push_undo(self.undo, state.snapshot(self.review, ids, label=label, cur=self.cur))
        self.redo.clear()

    def travel(self, direction: str) -> dict | None:
        """复用完整决定快照，在当前页面内撤销或重做。"""
        source, target = {"undo": (self.undo, self.redo), "redo": (self.redo, self.undo)}[direction]
        if not source:
            return None
        entry = source[-1]
        current = state.snapshot(self.review, entry["decisions"], label=entry["label"], cur=entry["cur"])
        state.undo(self.review, source)
        state.push_undo(target, current)
        return entry

    def resume_workspace(self, saved: dict) -> None:
        """可选的页面状态只用于同一对文稿，旧存档无需这些字段。"""
        if saved.get("original") != self.original or saved.get("revised") != self.revised:
            return
        workspace = saved.get("workspace")
        if not isinstance(workspace, dict):
            return
        drafts = workspace.get("drafts", {})
        if isinstance(drafts, dict):
            changes = self.by_id if drafts else {}
            self.drafts = {cid: text for cid, text in drafts.items() if cid in changes and isinstance(text, str)}
        saved_filter = workspace.get("filter")
        if saved_filter in state.FILTERS or (
            saved_filter == MODEL_FILTER and (self.reference_suggestions or self.model_suggestions is not None)
        ):
            self.flt = saved_filter
        if workspace.get("display") in ("proof", "compare") or (
            self.unpaired_rewrite and workspace.get("display") == "drafts"
        ):
            self.display = workspace["display"]
        self.search = workspace.get("search", "") if isinstance(workspace.get("search", ""), str) else ""
        self.notes_only = workspace.get("notes_only") is True
        current = workspace.get("current")
        if isinstance(current, str) and current in self.by_id:
            self.cur = current

    def decide(self, cid: str, status: str, text: str | None = None) -> None:
        self.record([cid], f"第 {number(cid)} 处 {DECISION_ZH[status]}")
        if status == "edited":
            self.drafts.pop(cid, None)
        state.set_decision(self.review, cid, status, text)

    def batch(self, ids: list[str], status: str) -> list[str]:
        targets = state.batch_targets(self.review, ids)
        if not targets:
            return []
        if status not in ("accepted", "rejected"):
            raise ValueError("批量操作只支持采用改稿或保留原稿")
        self.record(targets, f"批量{view_label(status)} {len(targets)} 处")
        return state.batch_decide(self.review, targets, status)

    def round_snapshot(self) -> dict:
        return {
            "original": self.original, "revised": self.revised, "title": self.title, "names": dict(self.names),
            "formats": dict(self.formats), "review": state.review_snapshot(self.review),
            "workspace": {"drafts": dict(self.drafts), "current": self.cur, "filter": self.flt,
                          "display": self.display, "search": self.search, "notes_only": self.notes_only},
        }

    def load_snapshot(self, saved: dict, report: dict) -> str | None:
        message = self.load(saved["original"], saved["revised"], report, title=saved["title"], names=saved["names"],
                            formats=saved["formats"], saved_review=saved["review"])
        self.resume_workspace(saved)
        return message

    def restore_project(self, saved: dict, report: dict) -> str | None:
        rounds = saved.get("rounds", [])
        if not isinstance(rounds, list) or any(not isinstance(round_, dict) for round_ in rounds):
            raise ValueError("轮次记录的格式不正确。")
        message = self.load_snapshot(saved, report)
        self.rounds = list(rounds)
        return message

    def advance(self, baseline: str, candidate: str, report: dict, *, feedback: str, destination: str,
                revision_options: dict | None = None) -> None:
        previous = self.round_snapshot()
        previous["sent"] = {"feedback": feedback, "destination": destination}
        if revision_options is not None:
            previous["sent"]["options"] = dict(revision_options)
            previous["sent"]["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        number = len(self.rounds) + 2
        fmt = self.formats.get("revised", "txt")
        self.load(baseline, candidate, report, title=self.title, formats={"original": fmt, "revised": fmt},
                  names={"original": f"第 {number - 1} 轮合并稿", "revised": f"第 {number} 轮候选稿"})
        self.rounds.append(previous)

    def persist(self) -> None:
        if self.save_enabled:
            revision = uuid.uuid4().hex
            self.storage[STORE_KEY] = {**self.round_snapshot(), "revision": revision, "rounds": list(self.rounds),
                                       "saved_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")}
            self.saved_revision = revision

    def forget(self) -> None:
        self.storage.pop(STORE_KEY, None)


def view_label(status: str) -> str:
    return DECISION_ZH["accepted" if status == "accepted" else "rejected"]
