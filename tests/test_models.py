"""full 模式（A2）与 Step 1 六对句子的模型特征核对。本地没有 .model-cache/ 时 skip。"""

import pytest

from acceptance_desk import audit, validate_report
from acceptance_desk.text import normalize

# experiments/step1_models.md “试跑结果”表：(原句, 改句, 原→改 蕴含/中立/矛盾, 改→原 蕴含/中立/矛盾, 余弦)
STEP1 = [
    ("本研究采用问卷调查的方法收集了三所高校学生的数据。", "本研究通过问卷调查，收集了三所高校学生的数据。",
     (0.994, 0.005, 0.001), (0.993, 0.007, 0.001), 0.982),
    ("实验结果表明，新方法在小样本条件下的准确率高于基线方法。", "实验结果显示，在小样本条件下，新方法的准确率比基线方法更高。",
     (0.986, 0.006, 0.009), (0.959, 0.010, 0.031), 0.972),
    ("实验结果表明，新方法在小样本条件下的准确率高于基线方法。", "实验结果表明，新方法在小样本条件下的准确率低于基线方法。",
     (0.001, 0.001, 0.998), (0.001, 0.003, 0.995), 0.961),
    ("受访者普遍支持延长图书馆的开放时间。", "受访者普遍反对延长图书馆的开放时间。",
     (0.000, 0.000, 0.999), (0.001, 0.002, 0.998), 0.906),
    ("睡眠不足可能是导致学生成绩下降的原因之一。", "睡眠不足是导致学生成绩下降的原因之一。",
     (0.545, 0.447, 0.008), (0.976, 0.023, 0.001), 0.992),
    ("这一结果可能说明政策干预对就业率有正向影响。", "这一结果说明政策干预对就业率有正向影响。",
     (0.859, 0.137, 0.003), (0.762, 0.234, 0.004), 0.987),
]
KEYS = ("entailment", "neutral", "contradiction")


@pytest.mark.parametrize("orig,rev,fwd,bwd,cos", STEP1)
def test_step1_pairs_reproduced(models, orig, rev, fwd, bwd, cos):
    f, b = models.nli(orig, rev), models.nli(rev, orig)
    # 文档保留 3 位小数，这里允许 0.0011 的舍入误差
    assert [f[k] for k in KEYS] == pytest.approx(fwd, abs=0.0011)
    assert [b[k] for k in KEYS] == pytest.approx(bwd, abs=0.0011)
    assert models.similarity(orig, rev) == pytest.approx(cos, abs=0.0011)


def test_a2_schema_example_full(models, model_cache, schema_example):
    inp, exp = schema_example
    o, r = normalize(inp["original"]), normalize(inp["revised"])
    rep = audit(o, r, mode="full", model_cache=model_cache)
    assert validate_report(rep, o, r) == []
    assert rep["run"]["mode"] == "full"
    assert rep["run"]["models"] == exp["run"]["models"]  # revision 与 Step 1 一致
    assert [(c["id"], c["alignment"], c["original"], c["revised"]) for c in rep["changes"]] == \
        [(c["id"], c["alignment"], c["original"], c["revised"]) for c in exp["changes"]]
    for c in rep["changes"]:
        nli, sim = c["features"]["nli"], c["features"]["similarity"]
        if c["alignment"] in ("0-1", "1-0"):
            assert nli is None and sim is None
            continue
        for d in ("forward", "backward"):
            assert abs(sum(nli[d].values()) - 1) <= 0.001
            assert all(v == round(v, 4) for v in nli[d].values())
        assert -1 <= sim <= 1
        assert c["decision"]["status"] == "pending"
        # 规则来源的原因与 rules_only 相同；nli 来源的原因只在 full 模式出现
        want = next(w for w in exp["changes"] if w["id"] == c["id"])
        assert [x for x in c["review"]["reasons"] if x["source"] != "nli"] == \
            [x for x in want["review"]["reasons"] if x["source"] != "nli"]


def test_auto_uses_models_when_available(models, model_cache):
    rep = audit("问卷可能有效。", "问卷有效。", model_cache=model_cache)
    assert rep["run"]["mode"] == "full"
    assert rep["changes"][0]["features"]["nli"]["forward"].keys() == set(KEYS)
