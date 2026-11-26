"""词表匹配与规则特征（A11–A21 及补充用例）。"""

import pytest

from acceptance_desk.align import Side
from acceptance_desk.lexicon import find_hits, find_numbers, load_lexicon
from acceptance_desk.rules import levenshtein, rule_features


@pytest.fixture(scope="module")
def lex():
    return load_lexicon()


def brief(hits):
    return [(h["category"], h["term"], h["text"], h["start"], h["end"]) for h in hits]


def feats(o, r, lex):
    return rule_features(Side(o, 0, len(o), 0), Side(r, 0, len(r), 0), lex)


def test_a11_overlap_longest(lex):
    assert brief(find_hits("在一定程度上提高了效率", lex)) == [("qualifiers", "在一定程度上", "在一定程度上", 0, 6)]


def test_a12_ignore_blocks(lex):
    assert brief(find_hits("这不可能成立，可能性很小", lex)) == [("certainty", "不可能", "不可能", 1, 4)]


def test_a13_ignore_related_research(lex):
    assert brief(find_hits("相关研究表明二者相关", lex)) == [("correlation", "相关", "相关", 8, 10)]


def test_a14_slop_vs_connective(lex):
    assert [(h["category"], h["term"]) for h in find_hits("由此可见，因此", lex)] == [
        ("slop", "由此可见"), ("connective", "因此")]


def test_a15_template(lex):
    t = "随着人工智能技术的快速发展"
    assert brief(find_hits(t, lex)) == [("slop", "随着…的快速发展", t, 0, len(t))]


def test_a16_template_not_cross_sentence(lex):
    assert find_hits("随着技术进步。社会的发展", lex) == []


def test_template_max_gap(lex):
    gap = "一" * (lex.max_gap + 1)
    assert find_hits(f"随着{gap}的发展", lex) == []
    assert len(find_hits(f"随着{'一' * lex.max_gap}的发展", lex)) == 1


def test_offset(lex):
    assert brief(find_hits("可能", lex, offset=10)) == [("qualifiers", "可能", "可能", 10, 12)]


def test_a17_counts(lex):
    f = feats("可能A，可能B。", "可能A，B。", lex)
    assert f["hedge_removed"] == ["可能"]
    assert f["delta"]["qualifiers"] == {"可能": -1}


def test_same_term_both_sides_cancels(lex):
    f = feats("结果可能说明问题。", "结果可能表明问题。", lex)
    assert f["hedge_removed"] == [] and f["delta"]["qualifiers"] == {}
    hit = [h for h in f["hits"] if h["term"] == "可能"]
    assert len(hit) == 2 and not any(h["in_changed_region"] for h in hit)


def test_a18_causal_upgrade(lex):
    assert feats("A与B相关。", "A导致B。", lex)["causal_upgrade"] is True


def test_causal_already_present_no_upgrade(lex):
    assert feats("A导致B，且与C相关。", "A导致B，且导致C。", lex)["causal_upgrade"] is True
    assert feats("A导致B。", "A确实导致B。", lex)["causal_upgrade"] is False


def test_a19_connective_not_causal(lex):
    f = feats("A与B相关。", "因此A与B相关。", lex)
    assert f["causal_upgrade"] is False
    assert f["connective_added"] == ["因此"]


def test_a20_numbers(lex):
    f = feats("研究[3]发现。", "研究[3]发现，2020 年增长 12％。", lex)
    assert f["numbers_added"] == ["2020年", "12%"]
    assert f["citations_added"] == []


def test_a21_author_year_citation(lex):
    f = feats("研究发现增长。", "研究（张三，2020）发现增长。", lex)
    assert f["citations_added"] == ["（张三，2020）"]
    assert f["numbers_added"] == []


def test_numbers_changed_and_removed(lex):
    f = feats("2019年增长3.5%，样本120人。", "2020年增长3.5%。", lex)
    assert f["numbers_added"] == ["2020年"]
    assert f["numbers_removed"] == ["2019年", "120人"]


def test_citation_removed_and_bracket_range(lex):
    f = feats("已有研究[1-3]和[5]支持。", "已有研究[5]支持。", lex)
    assert f["citations_removed"] == ["[1-3]"]
    assert f["numbers_removed"] == []


def test_fullwidth_numbers(lex):
    assert find_numbers("增长１２％，共３ 所", lex) == ["12%", "3所"]


def test_expanded_order_and_repeat(lex):
    f = feats("A。", "也许甲，可能乙，可能丙。", lex)
    assert f["hedge_added"] == ["也许", "可能", "可能"]


def test_insert_side_all_changed(lex):
    f = rule_features(Side("", 0, 0, 0), Side("总之可能如此。", 5, 12, 0), lex)
    assert all(h["in_changed_region"] and h["side"] == "revised" for h in f["hits"])
    assert [h["start"] for h in f["hits"]] == [5, 7]
    assert f["length_ratio"] is None and f["char_edit_ratio"] is None and f["causal_upgrade"] is False


def test_ratios(lex):
    f = feats("实验共进行了三论。", "实验共进行了三轮。", lex)
    assert (f["length_ratio"], f["char_edit_ratio"]) == (1.0, 0.111)
    assert levenshtein("kitten", "sitting") == 3


def test_defensive_and_slop(lex):
    f = feats("该政策降低了门槛。", "当然，值得注意的是，该政策降低了门槛。", lex)
    assert f["defensive_added"] == ["当然"]
    assert f["slop_added"] == ["值得注意的是"]


# ---- 回归：数字保留正负号（PR #14 审阅 1）

@pytest.mark.parametrize("text,expected", [
    ("相关系数为-0.5。", ["-0.5"]),
    ("变化为-12%。", ["-12%"]),
    ("变化为＋12％，或−3.5‰", ["+12%", "-3.5‰"]),   # 全角正号、Unicode 负号
    ("（－４）", ["-4"]),                             # 全角负号与全角数字
    ("比例为0.5", ["0.5"]),
    ("范围 1-3 年", ["1", "3年"]),                   # 范围里的连字符不是负号
    ("2019-2020年", ["2019", "2020年"]),
    ("COVID-19", ["19"]),
    ("-5~-3", ["-5", "-3"]),
    ("10%～20%", ["10%", "20%"]),
    ("约1.5e-3，即2.0E+8分之一", ["1.5e-3", "2.0E+8"]),  # 科学计数法整体提取
    ("共3×10^5个", ["3×10^5个"]),
    ("1,234人", ["1,234人"]),
])
def test_signed_numbers(lex, text, expected):
    assert find_numbers(text, lex) == expected


@pytest.mark.parametrize("o,r,added,removed", [
    ("相关系数为-0.5。", "相关系数为0.5。", ["0.5"], ["-0.5"]),
    ("变化为-12%。", "变化为12%。", ["12%"], ["-12%"]),
    ("变化为12%。", "变化为+12%。", ["+12%"], ["12%"]),
    ("区间为1-3年。", "区间为1-5年。", ["5年"], ["3年"]),
    ("误差约1.5e-3。", "误差约1.5e-4。", ["1.5e-4"], ["1.5e-3"]),
])
def test_sign_change_is_number_change(o, r, added, removed):
    from acceptance_desk import audit
    (c,) = audit(o, r, mode="rules_only")["changes"]
    rules = c["features"]["rules"]
    assert (rules["numbers_added"], rules["numbers_removed"]) == (added, removed)
    assert c["review"]["flagged"] is True
    codes = [x["code"] for x in c["review"]["reasons"]]
    assert "number_added" in codes and "number_removed" in codes


@pytest.mark.parametrize("text,expected", [
    ("在两所学校进行了三轮调查，共二十人。", ["两所", "三轮", "二十人"]),
    ("一百零二个人，历时十二个月、三个小时和五分钟。", ["一百零二个人", "十二个月", "三个小时", "五分钟"]),
    ("二〇二六年，负三倍，正两倍。", ["二〇二六年", "负三倍", "正两倍"]),
    ("三 轮，再做三轮。", ["三轮", "三轮"]),
    ("一方面万一不成立，统一理论仍需验证。", []),
    ("三点五轮、百分之三人、壹佰人。", []),
    ("两所[3]与３所。", ["两所", "3所"]),
    ("这是一个重要的问题，一个人很难在一年里第一次就做对。", []),          # 单独的“一”加量词不算数量
    ("十一个样本、一百个人、二十一年。", ["十一个", "一百个人", "二十一年"]),   # 以“一”开头或结尾的多位数照常识别
])
def test_chinese_quantities(lex, text, expected):
    assert find_numbers(text, lex) == expected


@pytest.mark.parametrize("original,revised,added,removed", [
    ("研究覆盖两所学校。", "研究覆盖三所学校。", ["三所"], ["两所"]),
    ("实验进行三轮。", "实验进行四轮。", ["四轮"], ["三轮"]),
    ("两所学校、两所幼儿园。", "两所学校。", [], ["两所"]),
    ("共两所学校。", "共2所学校。", ["2所"], ["两所"]),
    ("调查了一所学校。", "调查了两所学校。", ["两所"], []),                 # “一所”不识别，但改成“两所”仍被提示
])
def test_chinese_quantity_change_is_flagged(original, revised, added, removed):
    from acceptance_desk import audit
    report = audit(original, revised, mode="rules_only")
    rules = [c["features"]["rules"] for c in report["changes"]]
    assert [n for f in rules for n in f["numbers_added"]] == added
    assert [n for f in rules for n in f["numbers_removed"]] == removed
    assert all(c["review"]["flagged"] for c in report["changes"])


def test_lexicon_content_change_requires_version_bump():
    """词表内容（含数字、引用的正则）一旦改动，version 必须跟着改，否则旧报告的 lexicon_version 无法追溯。

    改词表时：同时改 lexicon.toml 里的 version，并在这里登记新版本的内容哈希。
    0.1 的内容自 2404e49（2026-11-08，数字正负号修复）起未变；首轮试标的归档报告是这一版。
    0.1.1（PR #31）加入带量词的中文整数，并排除单独的“一”加量词。
    0.1.2 在限定词里加入范围限定“主要”“纯粹”“单纯”。
    0.2 新增 attribution 类别（新增归因，Issue #51 方案 A：旧报告按它记录的版本取类别）。
    """
    import hashlib
    from acceptance_desk.lexicon import default_lexicon_path, load_lexicon
    known = {"0.1": "ec94d0459ab15bbaa5359a131a624d0fdd42e54ca7ce0c71c88885c0e2b78cb4",
             "0.1.1": "5a2327ae491937f705bd91d24f21adca4a030e90336944a8c4b340ef468ca28a",
             "0.1.2": "47aa6dcf6c7e0fb8b0ba5f35a0c94590be5f507be9376da6946be9b8d02d562e",
             "0.2": "bdeea3f4ac4ce9734ed7f63d995c0ec4ba9dd405e9d82fecf952aeb5139c21df"}
    path = default_lexicon_path()
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    version = load_lexicon().version
    assert known.get(version) == digest, f"词表内容变了但 version 仍是 {version}：请提升版本并登记新哈希"


@pytest.mark.parametrize("original, revised, term", [
    ("这不是单纯的职业身份。", "这不是职业身份。", "单纯"),        # 编写的例句
    ("本文主要指这一生活形式。", "本文指这一生活形式。", "主要"),
    ("这不是纯粹的教义复述。", "这不做教义复述。", "纯粹"),
])
def test_scope_qualifier_removal_is_flagged(original, revised, term):
    """0.1.2：删去范围限定会让所指范围变宽或定义改变，按“删去限定词”提示。"""
    from acceptance_desk import audit
    change = audit(original, revised, mode="rules_only")["changes"][0]
    assert change["features"]["rules"]["hedge_removed"] == [term]
    assert any(r["code"] == "hedge_removed" for r in change["review"]["reasons"])


@pytest.mark.parametrize("original, revised, hit", [
    ("这样形成的纪律是一种防御。", "这样形成的纪律，用某学者的话说，是一种防御。", ["用…的话说"]),   # 编写的例句
    ("这一点并不成立。", "正如作者所说，这一点并不成立。", ["正如…所说"]),
    ("在作者看来，这一点成立。", "在作者看来，这一点完全成立。", []),                               # 两侧都有，不算新增
])
def test_attribution_added_is_flagged(original, revised, hit):
    """0.2：新增归因（把一句话归到某人名下）单独提示，需核对原文有无依据。"""
    from acceptance_desk import audit, validate_report
    rep = audit(original, revised, mode="rules_only")
    change = rep["changes"][0]
    assert change["features"]["rules"]["attribution_added"] == hit
    assert any(r["code"] == "attribution_added" for r in change["review"]["reasons"]) == bool(hit)
    assert validate_report(rep, original, revised) == []


def test_reports_from_lexicons_without_attribution_still_validate():
    """方案 A：旧报告按它记录的词表版本校验，不因新增类别失效；新版本报告缺字段则报错。"""
    import copy
    from acceptance_desk import audit, validate_report
    o, r = "结论可能成立。", "结论成立。"
    rep = audit(o, r, mode="rules_only")
    old = copy.deepcopy(rep)
    old["run"]["lexicon_version"] = "0.1.2"
    for c in old["changes"]:
        c["features"]["rules"]["delta"].pop("attribution")
        c["features"]["rules"].pop("attribution_added")
    assert validate_report(old, o, r) == []
    broken = copy.deepcopy(old)
    broken["run"]["lexicon_version"] = "0.2"
    assert any("类别" in e for e in validate_report(broken, o, r))
    extra = copy.deepcopy(rep)
    extra["run"]["lexicon_version"] = "0.1.2"
    assert any("不应有 attribution_added" in e or "类别" in e for e in validate_report(extra, o, r))
