# 文风细分标注：导入与一致性比较

任务版本 `style_taxonomy_v1`，选项修订 2026-11-12（新增 `not_present`）。脚本：`experiments/style_labels.py`。

这套标注回答“两人对文风判断是否一致”，**不是内容高风险的参考答案**：不进入 `pilot.py measure`，不当 `needs_review`，不用于训练。

## 表格结构

每位标注员两个工作表，按列名读取，其他列（隐藏的上下文等）忽略：

| 工作表 | 必需列 |
|---|---|
| 文风判断 | `change_id`、`original_style_issue`、`revised_style_issue`、`annotation_status`；可带 `original`、`revised` 供核对 |
| 子类与证据 | `change_id`、`empty_setup`、`redundant_summary`、`unsupported_inflation`、`formulaic_structure`、`unnecessary_defensiveness`、`chat_format_residue`、`original_evidence`、`revised_evidence`、`note` |

取值：

- 两侧判断：`yes` / `no` / `uncertain`
- 状态：`done` / `needs_context`
- 六个子类：`added` / `worsened` / `retained` / `improved` / `removed` / `mixed` / `uncertain` / `not_present`

三种“没有”分开记：**空白**是还没检查，不计入任何比较；`not_present` 是检查过、两侧都没有；`uncertain` 是检查过、拿不准。

标 `done` 的行必须两侧判断和六个子类都填；某侧判 `yes` 或 `uncertain` 时该侧证据不能空。脚本另给“提醒”（例如判有问题但六类都是 `not_present`），只请标注员再看一眼，不替人改判。

## 用法

在 Google 表格里对每个工作表“下载 → CSV”，放到 `data/labels/`（不进仓库）。每次比较都用当时导出的文件，B 未完成时脚本只比较两人都标 `done` 的条目，并报告各自进度。

```bash
uv run --locked python experiments/style_labels.py check data/labels/style_A_judgement.csv data/labels/style_A_subtypes.csv --name A --units data/labels/p01_dg03_pilot_A.csv
uv run --locked python experiments/style_labels.py compare data/labels/style_A_judgement.csv data/labels/style_A_subtypes.csv data/labels/style_B_judgement.csv data/labels/style_B_subtypes.csv --units data/labels/p01_dg03_pilot_A.csv --disagreements data/labels/style_disagreements.csv --discussion data/labels/style_discussion.md
```

- `--units`：生成标注单元的那份 CSV。核对编号集合相同，判断表若带原句、改句则逐字相同。
- `compare` 输出逐字段的一致条数、κ、各自填 `uncertain` 的条数和取值分布。κ 只在两人一共用了两种以上取值时计算。
- `--disagreements`、`--discussion` 和 `check --out` 只写新文件，已存在（包括两人的原始导出）就拒绝。分歧清单留有“裁决”“裁决理由”两列，裁决另存，不改两人的原始答案。
- A/B 若带原改句或上下文，会先逐字核对（统一换行），同编号不同正文拒绝比较；缺原改句时必须用 `--units` 绑定同一批单元。正式批次建议始终传 `--units`；只有相同文本而没有全文身份信息时，脚本不能区分不同论文中完全相同的句子。比较结果登记身份核对方式和输入指纹。
- 同时导出分歧 CSV 和讨论稿时，先独占预留两条路径再写内容，任一路径冲突或写入失败撤回本批新文件；已有文件和原始导出不动。
- `--discussion`：按单元汇总的讨论稿，列出前后文、原改句、两人的全部字段（有分歧的标 ⚑）、各自证据与备注，每处分歧给一个待回答问题和空的裁决、理由行。只列事实，不替任何一方选择。
- 读取时每行格数必须与表头相同，多一格少一格都按错位拒绝。
- 快照若来自按区间取值的接口（例如表格连接器），行末的空单元格可能被省略，这一行比表头短。先用 `pad-snapshot` 生成派生副本再比较：

  ```bash
  uv run --locked python experiments/style_labels.py pad-snapshot 原快照.csv 派生副本.csv
  ```

  只在行末补缺失的 `note` 空格子，其余格子逐字照抄并重读核对；缺的是判断列或证据列、或行比表头长，都拒绝。原快照不改，副本只写新文件，命令打印两边的 SHA256。用表格自身的“下载 → CSV”得到的文件是满列的，不需要这一步。
- `check` 另提醒子类方向与两侧判断的矛盾，例如方向记 `retained`（两侧都有）却把原稿判 `no`。

30 条单篇样本上的 κ 只用来校准口径，不说明哪个子类该删，也不说明产品成不成立。

## 分歧裁决

讨论稿每个问题下填好“裁决”“理由”后，用 `adjudicate` 导入：

```bash
uv run --locked python experiments/style_labels.py adjudicate A_判断.csv A_子类.csv B_判断.csv B_子类.csv 填好的讨论稿.md --units 单元表.csv --decided-by "裁决人" --record 裁决记录.json --merged 合并标签.csv
```

- 用 A、B 快照重算分歧，逐题核对单元、问题原文、表内 A/B 值、裁决取值和理由非空；任何一处对不上都不写文件。表格被编辑器重新对齐空格不影响。
- 裁决记录写明裁决人、每项裁决与 A 还是 B 相同、理由和全部输入的哈希，另附按原始 A/B 算的独立一致性统计。合并标签在一致处沿用两人相同的值、分歧处用裁决；合并后的“完全一致”不是新的 κ。
- `--decided-by` 如实填写裁决人或裁决方式（例如“两人讨论后的共识”）。
- 合并标签只用于校准文风口径，不是内容高风险答案，不广播给成员记录，不作训练标签。
- 裁决导入要求 A、B 全部条目均为 `done` 且填写完整；空白或 `needs_context` 不进入完整合并标签。合并后的必填错误也会阻断输出；方向矛盾仍只提醒，不替人改判。裁决记录与合并标签作为一批输出，任一目标冲突时不留下另一份半批结果。

## 口径 v1.1（2026-11-13）

依据：p01 DeepSeek 末轮稿 30 个单元中 9 个分歧单元、28 项分歧，两人讨论后达成共识的裁决。下面按裁决理由整理成规则，措辞以两人确认为准。例句都是编写的。

### 可以写成规则的

1. **两侧判断看这一侧文本里有没有，不问是不是这次改出来的。** 原稿已有、改稿原样保留的问题，两侧都判 `yes`，子类方向记 `retained`。首轮分歧几乎都出在这一点上：一方只在“新增”时判有。
2. **`unnecessary_defensiveness`：预先否认上下文没有提出的说法，而且这句否认没有带来新的理由或判准，把正面论点往后推。** 三个条件同时满足才记。
   - 记：“本研究并不是要否认制度的作用，也不是要夸大个人选择。我们关注的是……”（前文没有人提出这两种说法，否认之后才进入正题。）
   - 不记：“有观点认为数字化必然提高效率。本文不这样看：两组数据显示……”（否认的是前文提出的说法，并且紧跟理由。）
   - 不记：“这里的‘相关’不是因果意义上的相关。”（否认本身给出了一个必要的区分。）
3. **`empty_setup`：只宣告接下来要做什么、本身没有内容的句子。** 包括泛泛承认对方有道理、但理由要到后文才出现的让步句。
   - 记：“下面要回答的正是这个问题。”“这一点值得进一步说明。”
   - 不记：“下面用两组问卷说明这一点：第一组……”（过渡句本身带了内容。）

### 不能推成规则的

- **句式不是标志。** “不是……而是”“并非”“不构成”“只要不”出现本身不说明有问题，要按上面第 2 条的三个条件判断。不能推成“凡否认句都算防御性表述”。
- **以下属于单例或个人写作偏好，记在理由里即可，不写成子类定义**：节奏拖沓、疑问词过多；某条内容放在脚注里是否合适；某处解释“没有必要”。
- **改写措辞不等于消除问题。** 改稿只替换了否认句里的词、结构不变时，按 `retained` 记。这是第 1 条的推论，不是新规则。

### 还没写进规则的两个细节

1. “堆砌多个抽象名词、没有推进论证”这次记在 `redundant_summary` 下。它属于冗余总结，还是六类之外的另一种问题？在决定前不改子类。
2. 有一处防御性表述 `retained` 的理由只写了节奏和疑问词，没有指出否认了哪个未提出的说法。按第 2 条，它是否仍成立？

## 验证边界

单元测试只用编写的示例行。真实数据：2026-11-13 用 p01 DeepSeek g03 的 A、B 只读快照（Codex 经表格连接器取值后序列化，各 30 条，均已标完）跑通 `check` 与 `compare`，编号和原改句与试标单元表逐字一致。其中 A 的子类表每行省略了末尾空 `note`，经 `pad-snapshot` 补齐后比较；另三份是满列。比较结果、讨论稿、派生副本和哈希保存在仓库外，不进仓库。
