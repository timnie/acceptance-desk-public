"""Export the previously frozen run03 rules candidate to a numeric JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from acceptance_desk.rule_classifier import RuleClassifier

if __package__:
    from .experiment_io import write_new_files
    from .train import file_sha, frozen_estimator, frozen_metadata
else:
    from experiment_io import write_new_files
    from train import file_sha, frozen_estimator, frozen_metadata

# Original frozen artifacts from 2026-11-16. Check both before any joblib deserialization.
RUN03_MODEL_SHA256 = "96e67c1b0629bdf384b52069882ca7808972252894657db030451672730baf80"
RUN03_METADATA_SHA256 = "9195785f85b980e81922d393cee7235870032f198b04dd22b1e191295d365bdd"


def export_model(model_dir: Path, out: Path) -> None:
    if (file_sha(model_dir / "classifier.joblib") != RUN03_MODEL_SHA256
            or file_sha(model_dir / "metadata.json") != RUN03_METADATA_SHA256):
        raise ValueError("请选择 run03 原始冻结模型中的 rules 目录，模型和元数据的 SHA-256 必须一致")
    meta = frozen_metadata(model_dir)
    pipeline = frozen_estimator(model_dir, meta)
    scaler, classifier = (estimator for _, estimator in pipeline.steps)
    signature, = meta["signatures"]
    payload = {
        "version": "1",
        "lexicon_version": json.loads(signature)["lexicon_version"],
        "features": meta["feature_names"],
        "threshold": meta["threshold"],
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "weights": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "source_model_sha256": RUN03_MODEL_SHA256,
        "source_metadata_sha256": RUN03_METADATA_SHA256,
    }
    RuleClassifier.from_json(payload)
    write_new_files([(out, json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n")])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        export_model(args.model_dir, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"[错误] {exc}\n")


if __name__ == "__main__":
    main()
