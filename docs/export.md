# 合并终稿与审阅记录（#18）接口

模块：`acceptance_desk/export.py`，从 `acceptance_desk` 顶层导出。审阅页只调用下面的公开函数，不直接拼字符串。

## 1. merge_decisions

```python
merge_decisions(report, original, revised, *, pending="block", layout="follow", markdown=False) -> MergeResult
```

| 参数 | 说明 |
|---|---|
| `report` | `audit()` 的报告，`decision` 已由审阅页回填（schema v0.1 §4.2） |
| `original` / `revised` | 审计时的两份全文。函数内先统一换行（schema §2.1），再核对 SHA256 |
| `pending` | `"block"`（默认）：有待定就抛 `PendingDecisions`，消息里写明条数，`.ids` 是待定编号；`"original"` / `"revised"`：待定处按原句 / 改句处理，结果标为草稿 |
| `layout` | 排版（空白）差异的处理，见 §4：`"follow"`（默认）/ `"original"` / `"revised"` |
| `markdown` | 为 `True` 时给排版差异加 Markdown 结构风险说明 |

`MergeResult` 的字段：

| 字段 | 说明 |
|---|---|
| `text` | 合并后的全文（`\n` 换行） |
| `draft` | 有待定条目、按策略处理时为 `True` |
| `pending_policy` | 本次使用的待定策略 |
| `counts` | 五种决定状态各多少条 |
| `pending_ids` | 待定条目的编号 |
| `layout` / `layout_choices` / `layout_gaps` | 排版差异列表（`LayoutDiff`）、每处实际取用的一侧、处数 |

满足以下性质（`tests/test_export.py`）：

- 全部采用 == 规范化改稿，全部保留 == 规范化原稿。测试覆盖：
  - 新增、删除、拆句、合句；
  - 拆段、并段（核心按“删除 + 新增”报告）；
  - 重复句；
  - 原稿或改稿为空、两者都为空、两者相同；
  - 句子只差空白、段首段尾空白、CRLF；
  - 300 篇随机生成的文稿。
- 混合决定逐条采用 `decision.final_text`。自行编辑成空字符串时，删去这一句及其前导空白。
- 以下情况抛 `MergeError`，不输出任何文本：
  - 文稿哈希不符；
  - 条目位置或文本与全文不符，或位置重复；
  - 报告里的修改与重新对齐的结果不一致；
  - 决定不合法：未知状态；采用 / 保留时的文本与改句 / 原句不符；已决定却缺文本；待定却带文本；
  - 对齐结果没有覆盖全部正文。

### 为什么要重新对齐

报告只列出修改项：每处修改有两侧的 `[start, end)`，但没有未修改的句子，也没有句间空白的对应关系。新增、删除的空侧只是一个锚点（schema §2.4），同一个锚点上可能同时有删除和新增。只靠报告里的区间，无法确定未修改部分该取哪一侧、按什么顺序拼接。

做法：

1. 用 `audit()` 同一套对齐，从两份全文重算完整序列（含未修改句）。
2. 按与 `align()` 相同的规则给每个修改项算出位置键，核对它与报告里的修改**集合完全相等**。
3. 两个游标沿原稿、改稿单调前进，逐项拼接：
   - 未修改句：照抄。
   - 修改句：取 `final_text`。
   - 句子之间只剩空白（实现里会检查，不是空白就拒绝合并）：取原稿侧还是改稿侧，跟随这段空白所属修改的决定；未修改句前的空白，以及全文末尾的空白，跟随前一处修改的决定。
   - 没有任何修改时（两份文稿只差空白），版式跟随改稿，并在 `layout_gaps` 里报告空白不同的位置数。

全程不做字符串查找或全局替换。

## 2. review_record_markdown

```python
review_record_markdown(report, result, *, title, names=None, formats=None, now=None) -> str
```

生成人可读的审阅记录，可直接贴进 AI 使用披露。依次包含：

- 标题：注明“完成版”或“草稿”。草稿另起一行写明待定条数和本次的处理方式。
- 文稿信息：原稿、改稿的文件名、格式、字数、SHA256 前 12 位，以及检查方式和版本。
- 决定汇总：各类决定的条数。
- 逐条记录：
  - 依据：模板文字。模型来源的原因只写“语义模型提示……”，不写概率。
  - 原句、改句：放进代码围栏，围栏比文本里最长的反引号串多一个，所以不会被提前闭合。
  - 采用的文本：仅自行编辑和草稿暂用时列出。
  - 用户备注。

进度恢复仍用 JSON 进度文件，这份 Markdown 不能导入。

## 3. 审阅页的导出

- “导出”弹出层：
  - 全部决定后，可下载完成版终稿。
  - 仍有待定时，面板显示条数和待定处采用的版本，默认选“原稿”。确认选项后点“下载草稿”。
- 终稿和草稿使用载入时所选的改稿格式（`.txt` 或 `.md`），导出的是按决定合并的源文本。
- 文件名：
  - 终稿：`<文稿名>_终稿.<后缀>`，草稿为 `<文稿名>_草稿.<后缀>`。
  - 审阅记录：`<文稿名>_审阅记录.md`，草稿为 `<文稿名>_审阅记录_草稿.md`。
  - 终稿或草稿若与上传的原稿或改稿同名，自动加 `_导出`。
  - 浏览器下载只生成新文件，不写回用户的源文件。

## 4. 排版（空白）差异

句子之外只剩空白。修改句前面的空白跟随该修改的决定，其余位置（未修改句之前、全文末尾）若两侧空白不同，就是一处**排版差异**。`layout_differences(report, original, revised, markdown=False)` 列出它们，每项包括：

- 两侧的空白原样；
- 位置：前一处修改的编号；
- 观察到的变化：空行数量变化、增删换行、换行与空格互换、行首缩进变化、空格或制表符变化。

`markdown=True` 时，另外标出可能影响结构的情况：

- 在代码围栏里；
- 行首缩进变化（可能改变列表层级或形成代码块）；
- 换行或空行变化（可能改变段落、列表或引用的分隔）。

这只是提示，不会自动处理。

处理规则（`layout`）：

| 取值 | 规则 |
|---|---|
| `follow`（默认） | 跟随前一处修改的决定：采用改句就用改稿排版，保留原句就用原稿排版；文首或没有任何修改时用改稿排版 |
| `original` | 所有排版差异统一用原稿的空白 |
| `revised` | 所有排版差异统一用改稿的空白 |

- 默认规则下，全部采用 == 改稿，全部保留 == 原稿。
- 两份文稿只差空白时 `changes` 为空，页面会显示“没有文字修改，但有 N 处排版差异”，不会写“完全一致”。
- 审阅记录里有一节“排版（空白）差异”，用 ⏎ · → □ 显示空白，并写明每处取用了哪一侧。

## 5. 已审阅记录的校验（`acceptance_desk/decisions.py`）

| 报告种类 | 用什么检查 | 要求 |
|---|---|---|
| `audit()` 刚输出的原始报告 | `validate_report()`（schema §9） | 未训练时每条都是 `pending` |
| 回填过决定的已审阅记录（进度文件、合并输入） | `check_reviewed_report()` / `reviewed_report_errors()` | 结构、哈希、编号、位置、决定都合法，不要求 `pending` |

合并终稿和页面导入进度文件共用 `decision_errors(change, trained=..., created_at=...)`，规则如下：

- `pending`：`final_text`、`decided_by`、`decided_at` 都为 `null`。
- `accepted` / `rejected` / `edited`：
  - `decided_by == "user"`，`decided_at` 是 ISO 8601 时间；
  - 采用时文本等于改句，保留时文本等于原句，编辑时必须有文本。
- `auto_passed`：只在 `run.classifier.status == "trained"` 且该条 `review.needs_review` 为 `false` 时允许；`decided_by == "system"`，`decided_at` 等于 `run.created_at`。未训练时出现就拒绝，防止未经确认的修改被当成系统已通过。
- `note` 必须是文本。

结构问题同样报为 `ReviewDataError`（`MergeError` 是它的子类），不会漏出未处理的异常。覆盖：

- 编号为空或重复；
- 位置不是整数（包括 `true` / `false`），或者是负数、越界；
- 位置与文本不符；
- 对齐类型未知；
- 缺少 `decision` 或 `review`；
- `changes` 不是列表，或者条目不是对象。

页面比较位置时也区分类型，`False` 不会被当成 `0`。

## 6. 文稿视图用的连续片段

`document_segments(report, original, revised)`：按原文顺序给出全部片段。每项包含：

- `change_id`：未修改句为 `None`；
- `gap`：本项前的空白；
- `original`、`revised`：两侧文本；
- `paragraph`：段落序号。

跳过删除句之后，`gap + revised` 依次拼接恰好等于改稿。审阅页的“文稿视图”只调用这个函数，不自行拼接。

## 原稿已有提示与作者修改意见（#35，共享核心）

这两种是独立的只读派生结果，不新增 AuditReport v0.1 字段。入口在 `acceptance_desk.hints`、`acceptance_desk.feedback`，也从包顶层导出。它们不会修改 `changes`、`summary`、风险字段、分类器特征、标注或训练数据；现有终稿与审阅记录的导出行为不变。当前页面尚未接入，新工作台可直接调用这些 Python API。

### 原稿已有提示

```python
from acceptance_desk import original_hints
hints = original_hints(report, original, revised)
```

输入必须是同一对原稿/改稿及其审计或已审阅记录。先统一换行并核对哈希、决定和完整对齐，再按词表的最左最长规则扫描原稿。只取 `slop` 和 `defensive`，且完整命中必须落在原改两侧相同的字符区间；包括未修改句和修改组中原样保留的部分。词内部发生插入/删除/替换、模板间隔变化、新增词、已删除词都不混进来。限定词不属于此列表。

每项包含稳定编号 `h001` 等、类别和词条、命中文字、原稿 `start/end`、对应的 `revised_start/revised_end`、原稿所在句及段落、所属 `change_id`（未改句为 null）和陈述性文案。位置是规范化后的 Python 字符半开区间，段落为零起点，与现有审计相同。不会写“应该删除”，也不估计风险。命中量不等于问题量。

词表版本须与报告相同。历史报告若使用旧词表，须通过 `lexicon_path` 显式提供当时版本，不能静默用当前词表重解释旧报告。版本标识的内容完整性仍由词表版本纪律保证。

### 作者修改意见

```python
from acceptance_desk import build_feedback, feedback_markdown, feedback_text
options = dict(title="文稿名", scope="local", selected_ids=["c001", "c003"])
structured = build_feedback(report, original, revised, **options)
markdown = feedback_markdown(report, original, revised, **options)
plain_text = feedback_text(report, original, revised, **options)
```

- `scope` 必须由作者显式选择：`local`（局部修订，其他文字尽量保持）或 `rewrite`（结合全文整体改写，已采用写法作参考）。不预选“禁止重写”。
- `selected_ids=None`：导出全部人工已决定项。指定列表时只导出这些组，仍按正文顺序组织；空列表允许，重复/未知 ID 拒绝。
- 待定默认不导出；确需附上，显式传 `include_pending=True`。它仍标为作者尚未决定，没有自动代填的目标文本。显式选择待定而未开启该参数会报错。
- 系统自动通过不算作者认可：默认略过；显式选择自动通过项会报错。
- 需恢复原稿和自行编辑的组先列，保留完整目标文本、原改引文、原文字符区间及段/句位置、检查依据和原样备注。长句定位引文仅取所有变化及前后约 20 字，并标明摘录区间；作者指定的恢复/编辑全文不能截短，空目标明确表示删除。
- 已采用组只列 ID、位置、开头至多 30 字和作者备注。局部修订要求保留，整体改写作为作者认可的写法参考。
- 默认带两侧同段的前一句上下文；`include_context=False` 可关闭，不跨段取句。
- 检查依据由 `change_evidence(change)` 分成 `observed/rules/model`；规则与模型判断不写成事实，不导出原始模型概率。现有页面未改，公开示例上已核对两边文案一致，后续界面可复用该核心函数。
- 文档写完整 SHA256、工具/词表版本和带时区的导出时间。传相同 `now` 时输出逐字相同；不传则用本机当前带时区时间。JSON 的 `feedback_version` 为 `1`，与 AuditReport 的 schema 版本不同。
- Markdown 的标题内容、原文、目标、依据和备注放入动态代码围栏，围栏长于内容中任何反引号串。TXT 用每行缩进区分引用，保留文字及换行。都只生成内容，不访问外部模型或发送意见。

### 离线命令行

锁定依赖同步后可用 `uv run --locked acceptance-desk-export ...`。也可用 `python -m acceptance_desk.feedback_cli ...`。输入和输出含原文，只放本地被忽略的私有目录：

```bash
uv run --locked acceptance-desk-export hints data/private/review.json data/private/original.txt data/private/revised.txt --out data/private/original-hints.json
uv run --locked acceptance-desk-export feedback data/private/review.json data/private/original.txt data/private/revised.txt --title "文稿名" --scope local --select c001 c003 --format markdown --out data/private/feedback.md
uv run --locked acceptance-desk-export feedback data/private/review.json data/private/original.txt data/private/revised.txt --title "文稿名" --scope rewrite --format txt --out data/private/feedback.txt
```

`--format json` 输出结构化意见。`--include-pending`、`--without-context` 和 `--now` 对应上述选项。UTF-8 输入支持 BOM；输出必须是新文件，错误不会覆盖输入或已有结果。缺失修改、文稿哈希不一致、旧对齐或非法决定均拒绝导出，不自动重算并替换作者决定。
