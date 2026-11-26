"""The ordered numeric features shared by offline training and workbench inference."""

from ._schema import ALIGNMENTS
from .lexicon import categories_for

LISTS = ("hedge_removed", "hedge_added", "certainty_added", "certainty_removed", "connective_added",
         "connective_removed", "defensive_added", "slop_added", "numbers_added", "numbers_removed",
         "citations_added", "citations_removed", "attribution_added")
# 词表 0.2 起才有 attribution_added。更早版本的报告没有这个字段：按 0 编码（只在该版本确实没有这一类时才允许），
# fit / merge 拒绝混用不同词表版本的数据，所以“没测”和“测了是 0”不会混在同一次训练里。
OPTIONAL_BY_CATEGORY = {"attribution_added": "attribution"}
RULES = [*LISTS, "causal_upgrade", "length_original", "length_revised", "length_ratio",
         "char_edit_ratio", "paired", *[f"align_{a}" for a in ALIGNMENTS]]
MODELS = [f"{d}_{p}" for d in ("forward", "backward") for p in ("entailment", "neutral", "contradiction")] + ["similarity", "model_present"]
FEATURES = {"rules": RULES, "models": MODELS, "all": RULES + MODELS}


def absent_allowed(name: str, lexicon_version: str) -> bool:
    cat = OPTIONAL_BY_CATEGORY.get(name)
    return cat is not None and cat not in categories_for(lexicon_version)


def vector(change: dict, lexicon_version: str) -> dict:
    rules, nli = change["features"]["rules"], change["features"]["nli"]
    values = {name: 0 if name not in rules and absent_allowed(name, lexicon_version) else len(rules[name])
              for name in LISTS}
    values |= {k: rules[k] if rules[k] is not None else 0 for k in
               ("length_original", "length_revised", "length_ratio", "char_edit_ratio")}
    values |= {"causal_upgrade": int(rules["causal_upgrade"]), "paired": int(change["alignment"] not in ("0-1", "1-0"))}
    values |= {f"align_{a}": int(change["alignment"] == a) for a in ALIGNMENTS}
    values |= {f"{d}_{p}": nli[d][p] if nli else 0 for d in ("forward", "backward")
               for p in ("entailment", "neutral", "contradiction")}
    values |= {"similarity": change["features"]["similarity"] or 0, "model_present": int(nli is not None)}
    return values
