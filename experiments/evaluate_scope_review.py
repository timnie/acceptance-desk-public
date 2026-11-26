"""Freeze, run and score scope review cases using the production request and parser."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from time import monotonic

from acceptance_desk import audit
from acceptance_desk._schema import text_sha256
from acceptance_desk.rule_classifier import RuleClassifier
from acceptance_desk.scope_review import (SUPPORT_BY_KIND, ReviewIntent, ScopeReviewError, ScopeReviewRequest,
                                          prepare_scope_review)

from .experiment_io import write_new_files

SCOPE_CHANGES = {"widened", "narrowed"}


@dataclass(frozen=True)
class EvaluationSuite:
    sha256: str
    cases: tuple[tuple[dict, ScopeReviewRequest], ...]


def prepare_cases(source: Path, baseline: RuleClassifier | None = None) -> EvaluationSuite:
    text = source.read_bytes().decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, list) or not data:
        raise ValueError("评测材料必须是非空案例数组。")
    cases = []
    seen = set()
    for row in data:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                for key in ("id", "original", "revised", "intent", "manual_question"))):
            raise ValueError("每个案例都要有编号、两版正文、作者要求和手工审阅问题。")
        case_id = row["id"]
        if case_id in seen:
            raise ValueError(f"评测案例编号重复：{case_id}")
        seen.add(case_id)
        original, revised = row["original"], row["revised"]
        report = audit(original, revised, mode="rules_only")
        request = prepare_scope_review(report, original, revised, [ReviewIntent("author-1", row["intent"])])
        expected = row.get("expected")
        if not request.unit_ids or not isinstance(expected, dict) or set(expected) != set(request.unit_ids):
            raise ValueError(f"{case_id} 的参考判断须覆盖所有审阅单元：{', '.join(request.unit_ids)}")
        for label in expected.values():
            if (not isinstance(label, list) or len(label) != 2 or any(not isinstance(v, str) for v in label)
                    or label[1] not in SUPPORT_BY_KIND.get(label[0], ())):
                raise ValueError(f"{case_id} 的参考分类与证据状态不匹配。")
        info = {"case_id": case_id, "request_id": request.request_id, "skill_version": request.skill_version,
                "original_sha256": request.original_sha256, "revised_sha256": request.revised_sha256,
                "intent_sha256": request.intent_sha256, "expected": expected,
                "change_ids_by_unit": request.change_ids_by_unit}
        if baseline is not None:
            suggestions = baseline.suggestions(report)
            info["run03_suggested_units"] = [uid for uid, members in request.change_ids_by_unit.items()
                                           if suggestions.intersection(members)]
        cases.append((info, request))
    return EvaluationSuite(text_sha256(text), tuple(cases))


def score_run(suite: EvaluationSuite, raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("suite_sha256") != suite.sha256:
        raise ValueError("模型答卷的材料指纹与这套评测不符。")
    if raw.get("source") not in ("fixture", "byok") or not isinstance(raw.get("model"), str) or not raw["model"].strip():
        raise ValueError("模型答卷须标明来源和实际使用的模型。")
    outputs = raw.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {info["case_id"] for info, _ in suite.cases}:
        raise ValueError("模型答卷的案例编号与评测不符。")

    totals = Counter(expected=0, valid_findings=0, anchored_findings=0, kind_matches=0, exact_matches=0,
                     missed_scope_changes=0, false_scope_changes=0, unsupported_called_sufficient=0,
                     manual_review=0, response_issues=0)
    rows = []
    for info, request in suite.cases:
        case_id = info["case_id"]
        item = outputs[case_id]
        if not isinstance(item, dict) or not (isinstance(item.get("raw"), str) or isinstance(item.get("error"), str)):
            raise ValueError(f"{case_id} 缺少模型原始输出或调用失败说明。")
        result = None
        if not isinstance(item.get("error"), str):
            try:
                result = request.parse_reply(item["raw"])
            except ScopeReviewError:
                pass
        findings = {finding.unit_id: finding for finding in result.findings} if result else {}
        issues = [issue.code for issue in result.issues] if result else ["case_failure"]
        totals["response_issues"] += len(issues)
        for uid, label in info["expected"].items():
            finding = findings.get(uid)
            actual = [finding.kind, finding.support] if finding else None
            totals["expected"] += 1
            if finding:
                totals["valid_findings"] += 1
                totals["anchored_findings"] += bool(finding.evidence)
                totals["kind_matches"] += actual[0] == label[0]
                totals["exact_matches"] += actual == label
            totals["manual_review"] += not finding or finding.kind == "uncertain"
            totals["missed_scope_changes"] += label[0] in SCOPE_CHANGES and (not finding or finding.kind != label[0])
            totals["false_scope_changes"] += bool(finding and finding.kind in SCOPE_CHANGES and label[0] not in SCOPE_CHANGES)
            totals["unsupported_called_sufficient"] += bool(
                finding and finding.support == "sufficient" and label[1] in {"insufficient", "uncertain"}
            )
            row = {"case_id": case_id, "unit_id": uid, "expected": label, "actual": actual,
                   "anchors": len(finding.evidence) if finding else 0,
                   "has_explanation": bool(finding and finding.effect)}
            if "run03_suggested_units" in info:
                row["run03_suggested"] = uid in info["run03_suggested_units"]
            rows.append(row)
    return {"source": raw["source"], "model": raw["model"], "effort": raw.get("effort"), "suite_sha256": suite.sha256,
            "skill_versions": sorted({request.skill_version for _, request in suite.cases}),
            "totals": dict(totals), "units": rows,
            "observations": [{"case_id": case_id, "elapsed_seconds": item.get("elapsed_seconds"),
                              "transport_failed": isinstance(item.get("error"), str)}
                             for case_id, item in outputs.items()]}


async def run_codex(suite: EvaluationSuite, model: str, effort: str, timeout: int) -> dict:
    from workbench.model_connection import Connection, ConnectionError

    executable = shutil.which("codex")
    if executable is None:
        raise ValueError("找不到已安装的 Codex CLI。")
    connection = Connection("codex", executable=executable).with_cli_model(model, "", effort)
    outputs = {}
    for info, request in suite.cases:
        started = monotonic()
        try:
            item = {"raw": await connection.generate(request.prompt, timeout=timeout)}
        except ConnectionError as exc:
            item = {"error": str(exc)}
        outputs[info["case_id"]] = dict(item, elapsed_seconds=round(monotonic() - started, 3))
    return {"model": model, "effort": effort, "source": "byok", "transport": "codex-cli",
            "suite_sha256": suite.sha256, "outputs": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("--run03", type=Path, help="optional frozen baseline; never sent to a model")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", type=Path, help="write hashes and reference classifications; no provider calls")
    mode.add_argument("--score", type=Path, help="read a local raw answer file; no provider calls")
    mode.add_argument("--codex-model", help="submit the case texts to the logged-in Codex CLI account")
    parser.add_argument("--raw-out", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--effort", default="", help="Codex CLI reasoning effort")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if args.timeout <= 0 or (args.codex_model and (not args.raw_out or not args.out)) or (args.score and not args.out):
        parser.error("运行需 --raw-out 和 --out，评分需 --out，--timeout 须为正整数。")
    paths = [path.resolve() for path in (args.raw_out, args.out, args.manifest) if path is not None]
    if len(paths) != len(set(paths)) or any(path.exists() or not path.parent.is_dir() for path in paths):
        parser.error("输出必须使用不同且不存在的路径，父目录须已存在。")

    try:
        baseline = RuleClassifier.read(args.run03) if args.run03 else None
        suite = prepare_cases(args.suite, baseline)
        if args.manifest:
            outputs = [(args.manifest, {"suite_sha256": suite.sha256, "cases": [info for info, _ in suite.cases]})]
        else:
            if args.codex_model:
                raw = asyncio.run(run_codex(suite, args.codex_model, args.effort, args.timeout))
            else:
                raw = json.loads(args.score.read_text(encoding="utf-8"))
            score = score_run(suite, raw)
            outputs = [(args.out, score)]
            if args.codex_model:
                outputs.insert(0, (args.raw_out, raw))
        write_new_files([(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n") for path, data in outputs])
    except (OSError, ValueError) as exc:
        print(f"评测未完成。{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
