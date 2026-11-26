# 演示用示例

Use the sample pairs below to try the review workflow without importing a private manuscript.

`original.txt` 和 `revised.txt` 是为演示编写的一对原稿和改稿，内容与数字都是虚构的，不来自任何论文。改稿里有意放入了以下几类修改：

| 修改 | 预期在报告里看到 |
|---|---|
| 删“可能”、加“直接”、“相关”改为“导致” | `hedge_removed`、`certainty_added`、`causal_upgrade` |
| 引用 `[4]` 换成 `（王明，2021）` | `citation_removed`、`citation_added` |
| 新增“当然，也要看到” | `defensive_added` |
| 一句拆成两句 | 对齐类型 `1-2` |
| 新增百分比 `92.5%` | `number_added` |
| 错字“三论”→“三轮” | 新增数量“三轮”的候选提示（错字修正也可能误报） |
| 删去末句、新增一句套话 | `sentence_deleted`；`sentence_inserted` 与 `slop_added` |

运行（仓库根目录）：

```bash
uv run acceptance-desk examples/original.txt examples/revised.txt --brief
uv run acceptance-desk examples/original.txt examples/revised.txt --out report.json
```

本地有 `.model-cache/` 且安装了 `.[models]` 时自动使用 NLI 和句向量（`--mode full` 强制使用）；否则退回 `rules_only`，并在标准错误输出提示原因。分类器尚未训练，所有修改都是 `pending`，由人逐条决定；候选原因只用来把修改分成“重点审阅”和“其余修改”两组。

## Markdown 示例

`original.md` 和 `revised.md` 是同一题材的 Markdown 版本，同样为虚构内容，用来演示审阅页的 .md 输入和终稿导出。审计按源码逐字进行，标题、列表、引用、链接、脚注、代码围栏的符号都算正文。预期结果：

| 修改 | 报告里看到 |
|---|---|
| 标题 `##` 改为 `###` | 一处修改，没有候选原因（符号变化同样列出） |
| 列表项“两所”改为“三所” | `number_removed`（两所）、`number_added`（三所） |
| 引用块里删“可能”、加“直接”、“相关”改为“导致” | `hedge_removed`、`certainty_added`、`causal_upgrade` |
| 链接文字“手册”改为“使用手册”（URL 不变） | 一处修改，没有候选原因 |
| 文末新增一句套话 | `sentence_inserted`、`slop_added` |

## 长示例

`long_original.txt` 和 `long_revised.txt` 是为检查长段落阅读编写的虚构章节：五段，共 13 处修改，其中 9 处被标为重点审阅（rules_only）。改稿中混有以下修改：

- 数字变动；
- 删除限定词；
- 新增确定性词；
- 新增防御性表述和引用；
- 合句；
- 删除整句；
- 新增套话。

另有两处规则没有标出（“显著高于”和因果化改写），可用来检查“其余修改”同样需要逐组确认。
