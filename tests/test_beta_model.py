"""The optional beta advises workbench users without rewriting audit or decisions."""

import json
from copy import deepcopy

import pytest

from acceptance_desk import audit, merge_decisions
from acceptance_desk.feature_vector import RULES, vector
from acceptance_desk.rule_classifier import RuleClassifier
from experiments import export_run03_beta
from review_ui.state import export_review
from workbench.session import MODEL_FILTER, STORE_KEY, Session


ORIGINAL = "采集3次样本。第一组观察鸟类。"
REVISED = "采集5次样本。第一组观察飞鸟。"


def payload(**changes):
    weights = [0] * len(RULES)
    weights[RULES.index("numbers_added")] = 2
    return {"version": "1", "features": RULES, "lexicon_version": "0.2", "threshold": 0.5,
            "mean": [0] * len(RULES), "scale": [1] * len(RULES), "weights": weights,
            "intercept": -1, **changes}


def load(session, report, saved=None):
    return session.load(ORIGINAL, REVISED, report, title="样例", names={}, formats={}, saved_review=saved)


def test_portable_scoring_preserves_frozen_boundary_and_extremes():
    model = RuleClassifier.from_json(payload(threshold=0.4762147615023482, intercept=-0.0952128180068963))
    zeros = dict.fromkeys(RULES, 0)
    assert model.score(zeros) == model.threshold
    assert RuleClassifier.from_json(payload(intercept=-1000)).score(zeros) == 0
    assert RuleClassifier.from_json(payload(intercept=1000)).score(zeros) == 1


@pytest.mark.parametrize("overrides", [{"features": list(reversed(RULES))}, {"threshold": True},
                                      {"weights": [float("nan")] * len(RULES)},
                                      {"scale": [0] * len(RULES)}, {"intercept": 10**1000}])
def test_rejects_invalid_numeric_model(overrides):
    with pytest.raises(ValueError):
        RuleClassifier.from_json(payload(**overrides))


def test_helpful_suggestions_and_state_interoperate_in_both_modes(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    report = audit(ORIGINAL, REVISED, mode="rules_only")
    before = deepcopy(report)
    session = Session({}, beta_model=RuleClassifier.read(path))
    load(session, report)
    expected = [change["id"] for change in report["changes"] if vector(change, "0.2")["numbers_added"]]
    assert expected and len(expected) < len(session.order)
    assert session.visible(MODEL_FILTER) == expected
    assert report == before and session.visible("pending") == session.order
    assert session.visible("flagged") == [c["id"] for c in report["changes"] if c["review"]["flagged"]]
    session.flt = MODEL_FILTER
    session.batch(expected, "rejected")
    session.persist()
    saved = session.storage[STORE_KEY]
    assert "model_suggestions" not in saved and "model_suggestions" not in saved["workspace"]

    beta = Session({}, beta_model=session.beta_model)
    load(beta, report, saved["review"])
    beta.resume_workspace(saved)
    assert beta.flt == MODEL_FILTER and beta.visible() == expected
    beta.restore(export_review(session.review))
    assert beta.visible(MODEL_FILTER) == expected

    regular = Session()
    load(regular, report, saved["review"])
    regular.resume_workspace(saved)
    assert regular.flt == "all" and regular.model_suggestions is None
    assert merge_decisions(regular.review, ORIGINAL, REVISED, pending="revised").text == (
        merge_decisions(beta.review, ORIGINAL, REVISED, pending="revised").text)


def test_incompatible_lexicon_falls_back_to_existing_audit():
    report = audit(ORIGINAL, REVISED, mode="rules_only")
    session = Session(beta_model=RuleClassifier.from_json(payload(lexicon_version="other-version")))
    load(session, report)
    assert session.model_suggestions is None and "词表版本" in session.model_error
    assert session.visible() == session.visible("pending") == session.order


def test_converter_rejects_untrusted_artifact_before_deserialization(tmp_path, monkeypatch):
    (tmp_path / "classifier.joblib").write_bytes(b"untrusted data")
    monkeypatch.setattr(export_run03_beta, "frozen_estimator", lambda *_: pytest.fail("must verify before loading"))
    out = tmp_path / "beta.json"
    with pytest.raises(ValueError, match="SHA-256"):
        export_run03_beta.export_model(tmp_path, out)
    assert not out.exists()
