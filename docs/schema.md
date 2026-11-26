# audit() 接口格式 v0.1

- 报告格式使用 `schema_version = "0.1"`，词表版本独立记录在 `run.lexicon_version`。
- `audit()` 输出对齐结果、特征和候选原因。默认报告中各条修改都待作者决定，`classification.status` 使用 v0.1 的 `untrained` 标记。
- §7 说明怎样读取历史分类字段。新工作流中的模型建议由作者核对，产品方向见 [ADR 0001](adr/0001-author-directed-model-review.md)。
- 示例数据：§8 的输入文本是编写的格式示例，不来自任何论文。示例中 `features.nli` 和 `features.similarity` 的数值是**格式示例**，统一取 0.05 的整数倍，不是模型输出。真实模型的试跑记录见 `experiments/step1_models.md`。其余字段（位置、命中、次数、比例、统计）由示例输入按本文规则算出，可作为验收用例。

## 1. 函数签名

```python
def audit(original: str, revised: str, *, mode: str = "auto",
          lexicon_path: str | None = None, model_cache: str | None = None) -> dict:
    """返回 AuditReport（§3），可直接 json.dumps(report, ensure_ascii=False)。

    mode:
      "auto"       模型可加载就用 full，否则用 rules_only
      "full"       必须加载两个模型，加载失败抛 RuntimeError
      "rules_only" 不加载模型
    """
```

文稿工作台 `workbench/` 和插件 `mcp_server.py` 只调用 `audit()`，不直接调用特征和分类器。

## 2. 文本、位置与空值约定

### 2.1 规范化

`audit()` 先把 `\r\n` 和 `\r` 统一成 `\n`，不做其他改动（不做 NFKC、不去首尾空白）。下文的所有位置、哈希和字数都基于**规范化后的全文**。

### 2.2 段落与句子

- 段落：按 `\n\s*\n`（含空白的空行）切分，只含空白的段落丢弃。段落编号从 0 开始，按原稿、改稿各自计数。
- 句子：在段落内按以下规则切分。句子区间不含首尾空白，纯空白的句子丢弃。
  1. 主要边界是 `。！？`，半角 `!?` 同样处理。连续的句末标点（如 `？！`、`。。`）合为一个边界。
  2. 紧跟边界的右引号和右括号（`”’」』）)】`）并入前一句。
  3. 在未闭合的括号（`（(【[`）内不断句，避免切开引用和括号里的说明。
  4. 分号 `；;` 不是边界，分句留在同一句内。
  5. 省略号 `……`、`…` 本身不是边界；后面紧跟 `。！？` 时，在那个句末标点处断开。
  6. 半角 `.` 永不作为边界，小数（`3.5`）和缩写不会被切开。
  7. 段内单个 `\n` 也断句。
- 重复句：同一句话在全文中出现多次时，按出现位置分别处理，各自对齐。实现时位置从切分过程直接得到，**不得**用 `str.find` 或 `str.index` 回查文本位置。

### 2.3 字符位置

- 位置是 Python `str` 下标（Unicode 码点，不是字节，也不是 UTF-16 单元），采用半开区间 `[start, end)`。
- `start`、`end` 是非 bool 的整数，满足 `0 <= start <= end <= len(该侧全文)`。不允许负数下标：Python 切片会把 `[-30:2]` 截成 `[0:2]`，只核对切片会漏掉这类错误。
- 每处修改都有 `original` 和 `revised` 两侧，每侧满足 `全文[start:end] == text`。
- 一侧跨多个句子时（`1-2`、`2-1`），区间从第一句的开头到最后一句的结尾，`text` 是这段全文切片（含句间字符）。
- 词表命中的 `start`、`end` 也是全文位置，满足 `该侧全文[start:end] == hit.text`。

### 2.4 新增与删除的定位

| 对齐类型 | 空的一侧 | 空侧区间 |
|---|---|---|
| `0-1` 新增 | `original.text == ""` | `start == end ==` 插入点：新增句在改稿中前一个已对齐句子，其原稿对应句的 `end`。前面没有已对齐句子时，取后一个已对齐句子原稿对应句的 `start`。都没有时取 0 |
| `1-0` 删除 | `revised.text == ""` | `start == end ==` 删除点：规则同上，方向相反 |

空侧的 `paragraph` 取插入点或删除点所在的段落；该侧全文为空时为 `null`。

### 2.5 null 与空值

- 所有字段**始终出现**，不省略键。
- 不适用的数值或对象填 `null`：rules_only 模式下的 `nli` 和 `similarity`；新增、删除时的 `nli`、`similarity`、`length_ratio`、`char_edit_ratio`；分类器未训练时的 `classification` 各项和 `summary.by_label`。
- 列表类字段没有内容时填 `[]`，不填 `null`；字典类填 `{}`。
- 文本字段空时为 `""`，不为 `null`。

### 2.6 空输入与无变化

| 输入 | 结果 |
|---|---|
| `audit("", "")` | `changes == []`，统计全为 0 |
| `audit("", 改稿)` | 每句一条 `0-1`，原稿侧区间都是 `[0, 0]`，`paragraph` 为 `null` |
| `audit(原稿, "")` | 每句一条 `1-0`，改稿侧区间都是 `[0, 0]`，`paragraph` 为 `null` |
| 原稿与改稿完全相同 | `changes == []` |

对齐后文本完全相同的句对不进 `changes`。只差空白的句对照常输出。跨段落移动的句子不做专门处理，输出为一条 `1-0` 加一条 `0-1`。

### 2.7 修改顺序与编号

`changes` 按 `(original.start, original.end, revised.start, revised.end)` 升序排列，排完后按顺序编号 `c001`、`c002`……，不足 1000 条用 3 位，超过则统一用 4 位。

### 2.8 数值格式

模型概率的键恰好为 `entailment`、`neutral`、`contradiction`，每项是 [0, 1] 内的有限数（不是 bool、NaN 或无穷），保留 4 位小数，三项之和与 1 的差不超过 0.001。`similarity` 是 [-1, 1] 内的有限数。`length_ratio`、`char_edit_ratio` 保留 3 位小数。时间用 ISO 8601，带时区。

## 3. 顶层结构 AuditReport

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | str | `"0.1"` |
| `run` | object | 运行信息（§3.1） |
| `document` | object | 全文指纹和规模（§3.2） |
| `summary` | object | 统计（§3.3），必须与 `changes` 一致 |
| `changes` | array[Change] | 每处修改一条（§4），按 §2.7 排序 |

### 3.1 run

| 字段 | 类型 | 说明 |
|---|---|---|
| `tool_version` | str | `acceptance_desk.__version__` |
| `mode` | str | 实际使用的模式：`"full"` 或 `"rules_only"`（`auto` 解析后的结果） |
| `created_at` | str | 本次运行时间 |
| `models` | object \| null | `{"nli": {"name", "revision"}, "embedding": {"name", "revision"}}`；rules_only 时为 `null` |
| `lexicon_version` | str | 词表 `version` |
| `classifier` | object | `{"status": "untrained" \| "trained", "name": str \| null, "threshold": float \| null}` |
| `review_config` | object | 候选原因中 NLI 规则的阈值：`nli_entailment_min`、`nli_contradiction_min`，以及 `tuned`（bool，是否已在验证集上选定并冻结；v0.1 取初始值 0.5、0.5，`tuned = false`）。阈值不在真实测试集上选择 |

### 3.2 document

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_sha256` / `revised_sha256` | str | 规范化全文的 UTF-8 SHA256（64 位十六进制），报告不存全文 |
| `original_chars` / `revised_chars` | int | 规范化全文的字符数（`len`） |
| `original_paragraphs` / `revised_paragraphs` | int | 段落数 |

### 3.3 summary

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_changes` | int | `len(changes)` |
| `flagged` | int | `review.flagged == true` 的条数（重点审阅） |
| `unflagged` | int | `total_changes - flagged`（其余修改） |
| `needs_review` | int | `review.needs_review == true` 的条数；未训练时等于 `total_changes` |
| `auto_passed` | int | `total_changes - needs_review`；未训练时为 0 |
| `by_alignment` | object | 五种对齐类型各自的条数，五个键都出现 |
| `by_reason` | object | 每个原因代码出现在多少条修改里（同一条只计一次），键按字母序，只列出现过的代码 |
| `by_label` | object \| null | 未训练时为 `null`；训练后列出五个标签代码各自的条数 |

## 4. Change

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 见 §2.7 |
| `alignment` | str | `"1-1"` 改写、`"1-2"` 拆句、`"2-1"` 合句、`"0-1"` 新增、`"1-0"` 删除 |
| `original` | Side | 原稿侧 |
| `revised` | Side | 改稿侧 |
| `features` | object | §5 |
| `classification` | object | §7 |
| `review` | object | §6 |
| `decision` | object | §4.2 |

### 4.1 Side

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | str | 该侧文本，空侧为 `""` |
| `start` / `end` | int | §2.3、§2.4 |
| `paragraph` | int \| null | §2.4 |

### 4.2 decision

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | str | `"pending"` 待定、`"accepted"` 接受改句、`"rejected"` 退回原句、`"edited"` 人工改写、`"auto_passed"` 自动通过 |
| `final_text` | str \| null | 最终采用的文本：pending 时为 `null`；auto_passed 和 accepted 时等于 `revised.text`；rejected 时等于 `original.text` |
| `decided_by` | str \| null | `"user"`、`"system"`；pending 时为 `null` |
| `decided_at` | str \| null | 决定时间；auto_passed 时等于 `run.created_at` |
| `note` | str | 用户备注，默认 `""` |

`audit()` 返回时：
- `needs_review == true` 的条目为 `pending`，`needs_review == false` 的条目为 `auto_passed`。
- 未训练阶段所有条目 `needs_review == true`，因此全部为 `pending`。没有规则命中只说明规则未触发，不代表修改无风险。
- 审阅页和插件只回填 `decision`。

## 5. features

```
features = {
  "rules": {...},                              # 任何模式都有
  "nli": {"forward": P, "backward": P} | null,
  "similarity": float | null
}
P = {"entailment": float, "neutral": float, "contradiction": float}
```

### 5.1 双向 NLI

- `forward`：前提 = `original.text`，假设 = `revised.text`（原→改）。蕴含概率低，说明改句说了原句推不出的内容。
- `backward`：前提 = `revised.text`，假设 = `original.text`（改→原）。两个方向一起给，用来区分“改句比原句说得更多”和“改句比原句说得更少”。
  - Step 1 的“删‘可能’”第一例：forward 中立 0.447，backward 蕴含 0.976。
  - 第二例为 forward 蕴含 0.859、中立 0.137，backward 蕴含 0.762、中立 0.234。
  - 两例的形态不同，详见 `experiments/step1_models.md`。双向概率作为特征交给分类器学习，接口不预设固定形态。
- 三个概率按 `config.id2label` 的名字取值，键名统一为小写的 `entailment`、`neutral`、`contradiction`。
- 新增、删除、rules_only 时为 `null`。

### 5.2 similarity

BAAI/bge-small-zh-v1.5 句向量：取最后一层 `[CLS]`，L2 归一化后做点积，范围 [-1, 1]。只作为分类器特征，不进入候选原因：Step 1 中两组明显矛盾句的余弦相似度为 0.906 和 0.961，相似度高不代表语义一致。

### 5.3 rules

| 字段 | 类型 | 说明 |
|---|---|---|
| `hits` | array[Hit] | 两侧全部词表命中，先原稿侧后改稿侧，各侧按 `start` 升序 |
| `delta` | object | 报告所用词表版本的全部类别都出现：0.2 起是八个（`qualifiers`、`certainty`、`correlation`、`causal`、`connective`、`defensive`、`slop`、`attribution`），0.1–0.1.2 是前七个；每类是 `{词条: 改稿次数 − 原稿次数}`，只列非 0 项 |
| `hedge_removed` / `hedge_added` | array[str] | `qualifiers` 的 delta 按次数展开（−2 展开为两个） |
| `certainty_added` / `certainty_removed` | array[str] | `certainty` 同上 |
| `connective_added` / `connective_removed` | array[str] | `connective` 同上 |
| `defensive_added` | array[str] | `defensive` 中次数增加的词条 |
| `slop_added` | array[str] | `slop` 中次数增加的词条 |
| `attribution_added` | array[str] | `attribution` 中次数增加的词条（新增归因）。词表 0.2 起才有；更早版本的报告没有这个字段 |
| `causal_upgrade` | bool | §5.4 |
| `numbers_added` / `numbers_removed` | array[str] | 规范化后的数字多重集之差 |
| `citations_added` / `citations_removed` | array[str] | 引用标记多重集之差 |
| `length_original` / `length_revised` | int | 两侧 `len(text)` |
| `length_ratio` | float \| null | `length_revised / length_original`；任一侧为空时为 `null` |
| `char_edit_ratio` | float \| null | 字符级 Levenshtein 距离 / `length_original`；任一侧为空时为 `null` |

展开列表中词条的顺序：按该词条在对应侧第一次出现的位置排列（删除类看原稿侧，新增类看改稿侧）。模板词条记模板本身，例如 `"随着…的快速发展"`。

Hit：

| 字段 | 类型 | 说明 |
|---|---|---|
| `side` | str | `"original"` 或 `"revised"` |
| `category` | str | 报告所用词表版本的类别之一（`ignore` 的命中不输出） |
| `term` | str | 词表词条（模板保留“…”） |
| `text` | str | 实际命中的文本 |
| `start` / `end` | int | 全文位置 |
| `in_changed_region` | bool | 命中区间是否与该侧的改动区间重叠 |

`in_changed_region` 用来把命中和改动上下文联系起来：用 `difflib.SequenceMatcher(None, 原句, 改句, autojunk=False)` 计算 opcodes，非 `equal` 段在各侧覆盖的字符就是改动区间。新增和删除时，该侧全部命中都算 `true`。两侧都出现、没被改动的词次数相互抵消，不进 delta；但仍保留在 `hits` 里，`in_changed_region` 为 `false`。

### 5.4 词表匹配规则

以下规则与 `acceptance_desk/resources/lexicon.toml` 文件头部的说明一致：

1. **最左最长、不重叠**：所有类别加 `ignore` 的词条合在一起，从左到右扫描；同一起点取最长命中；被命中的字符不再参与其他匹配。
   - 例：“在一定程度上”只算 `qualifiers`，其中的“一定”不再算 `certainty`；“不可能”算 `certainty`，其中的“可能”不再算 `qualifiers`；“由此可见”算 `slop`，其中的“由此”不再算 `connective`。
2. **ignore 挡误报**：`ignore` 的命中直接丢弃。例：“相关研究”不产生 `correlation` 命中，“可能性”不产生 `qualifiers` 命中。
3. **模板**：含“…”的词条按“…”切段，段与段之间隔 0 到 `matching.max_gap` 个字符，间隔里不含 `matching.gap_stop` 中的字符，也就是不跨句；命中文本包含间隔。
4. **按次数比较**：delta 是次数之差，不是“有没有”。原句“可能 A，可能 B”改成“可能 A，B”，得到 `hedge_removed = ["可能"]`。
5. **数字与引用**：先匹配两种引用正则，再在引用之外匹配数字。
   - 正负号：数字前紧挨着 `-`、`+`、`−`（U+2212）、`－`、`＋`、`﹣`，且符号前不是数字、字母或小数点时，符号算作数字的一部分。“-0.5”→“0.5”记为删去 `-0.5`、新增 `0.5`；“12%”→“+12%”也算变化（正号保留）。
   - 范围：“1-3”“2019-2020年”“COVID-19”中的连字符前面是数字或字母，不算负号，范围按两端分别提取（`1`、`3`）；“-5~-3”提取为 `-5`、`-3`。
   - 科学计数法 `1.5e-3`、`2.0E+8`、`3×10^5` 整体提取。
   - 规范化：全角数字、全角百分号、全角小数点转为半角，各种负号统一为 `-`、正号统一为 `+`，删除内部空白（“2020 年”记为 `2020年`）。证据中的数字就是规范化后的原文值，不做数值换算（`0.50` 与 `0.5` 视为不同）。
   - 中文数量：识别带明确量词的整数（如“两所”“三轮”“二十人”），保留字面值和多重集计数，不换算数值（“两所”→“2所”仍提示变化）。单独的“一”加量词（“一个”“一次”“第一次”）、裸中文数字、中文小数/分数、财务大写及上标指数（`10⁵`）不识别。量词清单见词表 `patterns.numbers`；属于候选信号，不自动判定事实改变。
6. **连接词与因果升级分开**：
   - `connective`（因此、从而、所以……）只记 `connective_added` 和 `connective_removed`；
   - `causal_upgrade = true` 当且仅当两侧都非空、`correlation` 的 delta 之和 < 0，且 `causal` 的 delta 之和 > 0；
   - 单独新增“因此”不触发因果升级，它作为 `connective_added` 进入候选原因，是否构成新推论由人判断。

## 6. review：候选标记与是否需要人决定

```
review = {"flagged": bool, "needs_review": bool, "basis": "untrained" | "classifier", "reasons": [Reason]}
Reason = {"source": "alignment" | "rule" | "nli", "code": str, "detail": str, "terms": [str]}
```

| 字段 | 含义 | 未训练（v0.1 当前） | 训练后 |
|---|---|---|---|
| `reasons` | 候选原因，注明来源 | 按下表生成 | 同左，作为解释 |
| `flagged` | 候选标记：界面上“重点审阅”与“其余修改”的分组依据 | `len(reasons) > 0` | 同左 |
| `needs_review` | 是否需要人决定 | 全部为 `true` | `risk_probability >= run.classifier.threshold` |
| `basis` | `needs_review` 的依据 | `"untrained"` | `"classifier"` |

- `flagged == false` 只表示规则和模型原因都没有触发，不表示修改无风险。未训练阶段这类修改仍为 `pending`，在界面上归入“其余修改”，由人接受或退回。
- 每条原因的 `detail` 是给人看的中文说明，由规则模板生成，不由模型生成文字。

`reasons` 按下表顺序排列：

| 顺序 | source | code | 触发条件 | terms |
|---|---|---|---|---|
| 1 | alignment | `sentence_inserted` | `alignment == "0-1"` | `[]` |
| 2 | alignment | `sentence_deleted` | `alignment == "1-0"` | `[]` |
| 3 | rule | `hedge_removed` | 列表非空 | 该列表 |
| 4 | rule | `hedge_added` | 列表非空 | 该列表 |
| 5 | rule | `certainty_added` | 列表非空 | 该列表 |
| 6 | rule | `connective_added` | 列表非空 | 该列表 |
| 7 | rule | `defensive_added` | 列表非空 | 该列表 |
| 8 | rule | `slop_added` | 列表非空 | 该列表 |
| 9 | rule | `number_added` | `numbers_added` 非空 | 该列表 |
| 10 | rule | `number_removed` | `numbers_removed` 非空 | 该列表 |
| 11 | rule | `citation_added` | `citations_added` 非空 | 该列表 |
| 12 | rule | `citation_removed` | `citations_removed` 非空 | 该列表 |
| 13 | rule | `attribution_added` | `attribution_added` 非空（词表 0.2 起） | 该列表 |
| 14 | rule | `causal_upgrade` | `causal_upgrade == true` | `[]` |
| 15 | nli | `nli_not_entailed` | `forward.entailment < review_config.nli_entailment_min` | `[]` |
| 16 | nli | `nli_contradiction` | `max(forward.contradiction, backward.contradiction) >= review_config.nli_contradiction_min` | `[]` |

`certainty_removed`、`connective_removed` 和 `similarity` 只作为特征，不单独触发候选。

词表版本与校验（Issue #51 方案 A）：`validate_report` 按报告 `run.lexicon_version` 取类别集合。0.1–0.1.2 的报告没有 `attribution` 类别和 `attribution_added` 字段，照旧通过；0.2 起的报告必须包含它们。新生成的报告默认用当前词表，旧报告作为归档保留，不改写。rules_only 模式下不产生 nli 来源的原因。

在当前审计中，候选原因用于排序和分组。作者逐项决定是否采纳。

## 7. classification：风险概率只来自训练后的分类器

| 字段 | 未训练（v0.1 当前） | 训练后 |
|---|---|---|
| `status` | `"untrained"` | `"trained"` |
| `label` | `null` | 五个标签代码之一（概率最大者） |
| `label_zh` | `null` | 对应中文名 |
| `probabilities` | `null` | 五个标签代码 → 概率，和为 1 |
| `risk_probability` | `null` | 前四类概率之和 |

历史报告的分类字段按以下约定读取：
- `review.basis = "classifier"`，`needs_review = risk_probability >= run.classifier.threshold`。`threshold` 在验证集上选定，配置冻结后才在真实测试集上评测。
- `needs_review == false` 的条目 `decision.status = "auto_passed"`。
- `reasons` 和 `flagged` 仍按 §6 生成，用于解释和分组。

未训练阶段任何字段都不出现风险分或概率。

标签代码：

| 代码 | 中文 | 高风险 |
|---|---|---|
| `semantic_change` | 语义篡改 | 是 |
| `stronger_claim` | 论证变强 | 是 |
| `weaker_claim` | 论证变弱 | 是 |
| `ai_slop` | AI slop | 是 |
| `no_risk` | 无风险 | 否 |

训练后单条 `classification` 的格式示例（数值为格式示例）：

```json
{
  "status": "trained",
  "label": "stronger_claim",
  "label_zh": "论证变强",
  "probabilities": {"semantic_change": 0.10, "stronger_claim": 0.70, "weaker_claim": 0.05, "ai_slop": 0.05, "no_risk": 0.10},
  "risk_probability": 0.90
}
```

## 8. 完整例子（未训练阶段，mode = full）

输入（编写的格式示例）：

```json
{
  "original": "本研究采用问卷调查收集数据。问卷结果可能说明，线上讨论频率与期末成绩相关。本文建议增加讨论环节。\n\n该方法在小样本场景下表现较好。实验共进行了三论。本章最后说明数据来源。",
  "revised": "本研究采用问卷调查收集数据。问卷结果说明，线上讨论频率直接导致了期末成绩的提升。因此，本文建议增加讨论环节。总的来说，这一发现具有重要的理论意义和现实意义。\n\n该方法在小样本场景下表现较好，准确率比基线高出 12%。实验共进行了三轮。"
}
```

输出。位置、命中、次数、比例、统计由上面的输入按本文规则算出；`nli`、`similarity` 为格式示例：

```json
{
  "schema_version": "0.1",
  "run": {
    "tool_version": "0.0.1",
    "mode": "full",
    "created_at": "2026-11-08T10:00:00+08:00",
    "models": {
      "nli": {
        "name": "IDEA-CCNL/Erlangshen-Roberta-110M-NLI",
        "revision": "864d25be3ce5e90d9193cb49d9fbd52722cdc6b0"
      },
      "embedding": {
        "name": "BAAI/bge-small-zh-v1.5",
        "revision": "7999e1d3359715c523056ef9478215996d62a620"
      }
    },
    "lexicon_version": "0.2",
    "classifier": {
      "status": "untrained",
      "name": null,
      "threshold": null
    },
    "review_config": {
      "nli_entailment_min": 0.5,
      "nli_contradiction_min": 0.5,
      "tuned": false
    }
  },
  "document": {
    "original_sha256": "f5ef30e248454b937b039123fbd9501a688ba95ca264e4b5934d8c9dc30fe75d",
    "revised_sha256": "cd4eeae6ee459da89f4165c5c7755343fee4e264376d9d10afeca2dd88204a58",
    "original_chars": 85,
    "revised_chars": 117,
    "original_paragraphs": 2,
    "revised_paragraphs": 2
  },
  "summary": {
    "total_changes": 6,
    "flagged": 6,
    "unflagged": 0,
    "needs_review": 6,
    "auto_passed": 0,
    "by_alignment": {
      "1-1": 4,
      "1-2": 0,
      "2-1": 0,
      "0-1": 1,
      "1-0": 1
    },
    "by_reason": {
      "causal_upgrade": 1,
      "certainty_added": 1,
      "connective_added": 1,
      "hedge_removed": 1,
      "nli_not_entailed": 2,
      "number_added": 2,
      "sentence_deleted": 1,
      "sentence_inserted": 1,
      "slop_added": 1
    },
    "by_label": null
  },
  "changes": [
    {
      "id": "c001",
      "alignment": "1-1",
      "original": {
        "text": "问卷结果可能说明，线上讨论频率与期末成绩相关。",
        "start": 14,
        "end": 37,
        "paragraph": 0
      },
      "revised": {
        "text": "问卷结果说明，线上讨论频率直接导致了期末成绩的提升。",
        "start": 14,
        "end": 40,
        "paragraph": 0
      },
      "features": {
        "rules": {
          "hits": [
            {
              "side": "original",
              "category": "qualifiers",
              "term": "可能",
              "text": "可能",
              "start": 18,
              "end": 20,
              "in_changed_region": true
            },
            {
              "side": "original",
              "category": "correlation",
              "term": "相关",
              "text": "相关",
              "start": 34,
              "end": 36,
              "in_changed_region": true
            },
            {
              "side": "revised",
              "category": "certainty",
              "term": "直接",
              "text": "直接",
              "start": 27,
              "end": 29,
              "in_changed_region": true
            },
            {
              "side": "revised",
              "category": "causal",
              "term": "导致",
              "text": "导致",
              "start": 29,
              "end": 31,
              "in_changed_region": true
            }
          ],
          "delta": {
            "qualifiers": {
              "可能": -1
            },
            "certainty": {
              "直接": 1
            },
            "correlation": {
              "相关": -1
            },
            "causal": {
              "导致": 1
            },
            "connective": {},
            "defensive": {},
            "slop": {},
            "attribution": {}
          },
          "hedge_removed": [
            "可能"
          ],
          "hedge_added": [],
          "certainty_added": [
            "直接"
          ],
          "certainty_removed": [],
          "causal_upgrade": true,
          "connective_added": [],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [],
          "attribution_added": [],
          "numbers_added": [],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 23,
          "length_revised": 26,
          "length_ratio": 1.13,
          "char_edit_ratio": 0.435
        },
        "nli": {
          "forward": {
            "entailment": 0.45,
            "neutral": 0.5,
            "contradiction": 0.05
          },
          "backward": {
            "entailment": 0.9,
            "neutral": 0.05,
            "contradiction": 0.05
          }
        },
        "similarity": 0.9
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "rule",
            "code": "hedge_removed",
            "detail": "删去限定词“可能”",
            "terms": [
              "可能"
            ]
          },
          {
            "source": "rule",
            "code": "certainty_added",
            "detail": "新增确定性词“直接”",
            "terms": [
              "直接"
            ]
          },
          {
            "source": "rule",
            "code": "causal_upgrade",
            "detail": "相关类表述减少、因果谓词增加",
            "terms": []
          },
          {
            "source": "nli",
            "code": "nli_not_entailed",
            "detail": "原句不蕴含改句（原→改 蕴含概率低于 nli_entailment_min）",
            "terms": []
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    },
    {
      "id": "c002",
      "alignment": "1-1",
      "original": {
        "text": "本文建议增加讨论环节。",
        "start": 37,
        "end": 48,
        "paragraph": 0
      },
      "revised": {
        "text": "因此，本文建议增加讨论环节。",
        "start": 40,
        "end": 54,
        "paragraph": 0
      },
      "features": {
        "rules": {
          "hits": [
            {
              "side": "revised",
              "category": "connective",
              "term": "因此",
              "text": "因此",
              "start": 40,
              "end": 42,
              "in_changed_region": true
            }
          ],
          "delta": {
            "qualifiers": {},
            "certainty": {},
            "correlation": {},
            "causal": {},
            "connective": {
              "因此": 1
            },
            "defensive": {},
            "slop": {},
            "attribution": {}
          },
          "hedge_removed": [],
          "hedge_added": [],
          "certainty_added": [],
          "certainty_removed": [],
          "causal_upgrade": false,
          "connective_added": [
            "因此"
          ],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [],
          "attribution_added": [],
          "numbers_added": [],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 11,
          "length_revised": 14,
          "length_ratio": 1.273,
          "char_edit_ratio": 0.273
        },
        "nli": {
          "forward": {
            "entailment": 0.95,
            "neutral": 0.05,
            "contradiction": 0.0
          },
          "backward": {
            "entailment": 0.95,
            "neutral": 0.05,
            "contradiction": 0.0
          }
        },
        "similarity": 0.95
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "rule",
            "code": "connective_added",
            "detail": "新增推论连接词“因此”",
            "terms": [
              "因此"
            ]
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    },
    {
      "id": "c003",
      "alignment": "0-1",
      "original": {
        "text": "",
        "start": 48,
        "end": 48,
        "paragraph": 0
      },
      "revised": {
        "text": "总的来说，这一发现具有重要的理论意义和现实意义。",
        "start": 54,
        "end": 78,
        "paragraph": 0
      },
      "features": {
        "rules": {
          "hits": [
            {
              "side": "revised",
              "category": "slop",
              "term": "总的来说",
              "text": "总的来说",
              "start": 54,
              "end": 58,
              "in_changed_region": true
            },
            {
              "side": "revised",
              "category": "slop",
              "term": "具有重要的理论意义和现实意义",
              "text": "具有重要的理论意义和现实意义",
              "start": 63,
              "end": 77,
              "in_changed_region": true
            }
          ],
          "delta": {
            "qualifiers": {},
            "certainty": {},
            "correlation": {},
            "causal": {},
            "connective": {},
            "defensive": {},
            "slop": {
              "总的来说": 1,
              "具有重要的理论意义和现实意义": 1
            },
            "attribution": {}
          },
          "hedge_removed": [],
          "hedge_added": [],
          "certainty_added": [],
          "certainty_removed": [],
          "causal_upgrade": false,
          "connective_added": [],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [
            "总的来说",
            "具有重要的理论意义和现实意义"
          ],
          "attribution_added": [],
          "numbers_added": [],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 0,
          "length_revised": 24,
          "length_ratio": null,
          "char_edit_ratio": null
        },
        "nli": null,
        "similarity": null
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "alignment",
            "code": "sentence_inserted",
            "detail": "新增整句",
            "terms": []
          },
          {
            "source": "rule",
            "code": "slop_added",
            "detail": "新增套话“总的来说”“具有重要的理论意义和现实意义”",
            "terms": [
              "总的来说",
              "具有重要的理论意义和现实意义"
            ]
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    },
    {
      "id": "c004",
      "alignment": "1-1",
      "original": {
        "text": "该方法在小样本场景下表现较好。",
        "start": 50,
        "end": 65,
        "paragraph": 1
      },
      "revised": {
        "text": "该方法在小样本场景下表现较好，准确率比基线高出 12%。",
        "start": 80,
        "end": 108,
        "paragraph": 1
      },
      "features": {
        "rules": {
          "hits": [],
          "delta": {
            "qualifiers": {},
            "certainty": {},
            "correlation": {},
            "causal": {},
            "connective": {},
            "defensive": {},
            "slop": {},
            "attribution": {}
          },
          "hedge_removed": [],
          "hedge_added": [],
          "certainty_added": [],
          "certainty_removed": [],
          "causal_upgrade": false,
          "connective_added": [],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [],
          "attribution_added": [],
          "numbers_added": [
            "12%"
          ],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 15,
          "length_revised": 28,
          "length_ratio": 1.867,
          "char_edit_ratio": 0.867
        },
        "nli": {
          "forward": {
            "entailment": 0.15,
            "neutral": 0.8,
            "contradiction": 0.05
          },
          "backward": {
            "entailment": 0.9,
            "neutral": 0.1,
            "contradiction": 0.0
          }
        },
        "similarity": 0.85
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "rule",
            "code": "number_added",
            "detail": "新增数字“12%”",
            "terms": [
              "12%"
            ]
          },
          {
            "source": "nli",
            "code": "nli_not_entailed",
            "detail": "原句不蕴含改句（原→改 蕴含概率低于 nli_entailment_min）",
            "terms": []
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    },
    {
      "id": "c005",
      "alignment": "1-1",
      "original": {
        "text": "实验共进行了三论。",
        "start": 65,
        "end": 74,
        "paragraph": 1
      },
      "revised": {
        "text": "实验共进行了三轮。",
        "start": 108,
        "end": 117,
        "paragraph": 1
      },
      "features": {
        "rules": {
          "hits": [],
          "delta": {
            "qualifiers": {},
            "certainty": {},
            "correlation": {},
            "causal": {},
            "connective": {},
            "defensive": {},
            "slop": {},
            "attribution": {}
          },
          "hedge_removed": [],
          "hedge_added": [],
          "certainty_added": [],
          "certainty_removed": [],
          "causal_upgrade": false,
          "connective_added": [],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [],
          "attribution_added": [],
          "numbers_added": [
            "三轮"
          ],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 9,
          "length_revised": 9,
          "length_ratio": 1.0,
          "char_edit_ratio": 0.111
        },
        "nli": {
          "forward": {
            "entailment": 0.95,
            "neutral": 0.05,
            "contradiction": 0.0
          },
          "backward": {
            "entailment": 0.95,
            "neutral": 0.05,
            "contradiction": 0.0
          }
        },
        "similarity": 0.95
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "rule",
            "code": "number_added",
            "detail": "新增数字“三轮”",
            "terms": [
              "三轮"
            ]
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    },
    {
      "id": "c006",
      "alignment": "1-0",
      "original": {
        "text": "本章最后说明数据来源。",
        "start": 74,
        "end": 85,
        "paragraph": 1
      },
      "revised": {
        "text": "",
        "start": 117,
        "end": 117,
        "paragraph": 1
      },
      "features": {
        "rules": {
          "hits": [],
          "delta": {
            "qualifiers": {},
            "certainty": {},
            "correlation": {},
            "causal": {},
            "connective": {},
            "defensive": {},
            "slop": {},
            "attribution": {}
          },
          "hedge_removed": [],
          "hedge_added": [],
          "certainty_added": [],
          "certainty_removed": [],
          "causal_upgrade": false,
          "connective_added": [],
          "connective_removed": [],
          "defensive_added": [],
          "slop_added": [],
          "attribution_added": [],
          "numbers_added": [],
          "numbers_removed": [],
          "citations_added": [],
          "citations_removed": [],
          "length_original": 11,
          "length_revised": 0,
          "length_ratio": null,
          "char_edit_ratio": null
        },
        "nli": null,
        "similarity": null
      },
      "classification": {
        "status": "untrained",
        "label": null,
        "label_zh": null,
        "probabilities": null,
        "risk_probability": null
      },
      "review": {
        "flagged": true,
        "needs_review": true,
        "basis": "untrained",
        "reasons": [
          {
            "source": "alignment",
            "code": "sentence_deleted",
            "detail": "删除整句",
            "terms": []
          }
        ]
      },
      "decision": {
        "status": "pending",
        "final_text": null,
        "decided_by": null,
        "decided_at": null,
        "note": ""
      }
    }
  ]
}
```

示例覆盖的情形：

| id | 对齐 | 要点 |
|---|---|---|
| c001 | 1-1 | 删“可能”、加“直接”、“相关”→“导致”触发 `causal_upgrade` |
| c002 | 1-1 | 只新增连接词“因此”：`connective_added`，`causal_upgrade = false` |
| c003 | 0-1 | 新增整句；原稿侧为零宽区间，位于 c002 原句的 `end`；命中两条套话 |
| c004 | 1-1 | 新增数字 `12%` |
| c005 | 1-1 | 错别字“三论”→“三轮”：产生新增数量“三轮”的候选提示，`flagged = true`；这也说明规则会误报，不能把候选提示当作事实改变的标签 |
| c006 | 1-0 | 删除整句；改稿侧为零宽区间，位于 c005 改句的 `end` |

## 9. 一致性检查清单（实现与审阅页都按这份清单验收）

1. 每条 `changes[i]` 两侧的 `start`、`end` 是非 bool 整数且 `0 <= start <= end <= len(全文)`，满足 `全文[start:end] == text`；空侧 `start == end`。
2. `changes` 满足 §2.7 的排序，`id` 连续。
3. 每个 Hit 的 `start`、`end` 同样是合法整数区间，满足 `该侧全文[start:end] == text`，且落在该侧区间内。
4. `delta` 等于由 `hits` 计数得出的差值；展开列表与 `delta` 一致。
5. `summary` 各项与 `changes` 重算的结果一致。
6. 未训练时：`classification` 各值为 `null`，`summary.by_label` 为 `null`，`run.classifier.status == "untrained"`。
7. `flagged == (len(reasons) > 0)`。未训练时，所有条目 `needs_review == true`、`decision.status == "pending"`，`summary.auto_passed == 0`；训练后，`needs_review == false` 的条目为 `"auto_passed"`。
8. rules_only 时：`run.models`、所有 `nli`、`similarity` 为 `null`，且没有 nli 来源的原因。
9. `nli` 非空时恰有 `forward`、`backward` 两组概率，每组符合 §2.8（键名、有限值、单项在 [0, 1]、和为 1）；`similarity` 非空时为 [-1, 1] 内的有限数。

## 10. 独立派生结果（#35）

`original_hints()` 的存量提示和 `build_feedback()` / `feedback_markdown()` / `feedback_text()` 的作者意见均在 AuditReport 之外生成，不增加或修改本接口字段。存量提示不进入 `changes`、风险计数、进度、分类器特征或训练评测。作者要导出的 ID 和修订范围由调用参数单独传入；不把系统自动通过当作作者认可，不修改原始 `decision`。新 API 与 CLI 见 `docs/export.md`。
