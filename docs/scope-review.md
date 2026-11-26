# 结论范围审阅接口与验收

原工作台可用 rules_only 模式打开仓库的虚构材料并做两版对校。审阅接口独立于原有报告、决定和保存格式。[工作台手册](workbench.md)

## 向工作台交接

1. 用现有两版正文和对应审计报告调用 `prepare_scope_review(report, original, revised, intents)`。传入的每条 `ReviewIntent(id, text)` 应经作者确认。作者待定或机器推测的要求保存在界面自己的状态里。可以选一部分审阅单元，在 `unchecked_unit_ids` 看本轮未送审的单元。
2. 在发送确认页显示 `request.payload` 的两版段落、改动单元、作者要求和所选目的地。两版全文都作为语境输入，即使只选部分单元。输入超过 100,000 字符时会明确报错。请求中段号从 `o1` 与 `r1` 开始。引用位置使用规范化换行后的字符半开区间。
3. 确认后调用 `await run_scope_review(connection, request, is_current=...)`，复用已有 BYOK 连接。生成候选稿和审阅使用不同请求。让 `is_current` 同时检查窗口的操作编号、两版文稿和当前作者要求。收到结果前或应用结果前发生切换时，按当前稿件重新准备。超时和连接失败交给界面提示，允许作者重试。
4. 在目录里用 `Finding.change_ids` 关联修改项，用 `Evidence.side/start/end` 定位原稿和改稿。引用可另落在其他段落。用纯文本显示模型的推断、作者确认过的要求、具体问题和缺少的材料。将 `issues`、`unchecked_unit_ids` 和 `uncertain` 项显示为待核对。引用有效只证明文字可定位，支撑是否成立仍由作者检查。
5. 作者用现有控件作出决定。审阅结果在本轮以外只用于帮助作者找回依据。一个模型结果可能跨多个改动编号，模型审阅单元也可能比作者心里的论证更窄。记录模型的范围争议和原人工决定，供下一轮核对。

请求标识由两版文稿、作者要求、选中单元和 Skill 内容共同确定。响应必须原样回传标识和 Skill 版本。每个单元至多一个有效结论。找不到原文、同一段有多处相同引文、未知作者要求、漏报单元、同一单元重复结果，均保留待人工核对的提示。模型无法明确定位时，作者仍有完整对校和导出入口。

### 最小调用

```python
from acceptance_desk import audit
from acceptance_desk.scope_review import ReviewIntent, prepare_scope_review
from workbench.scope_review import run_scope_review

request = prepare_scope_review(
    audit(original, revised, mode="rules_only"),
    original,
    revised,
    [ReviewIntent("i1", author_confirmed_instruction)],
)
result = await run_scope_review(connection, request, is_current=is_still_current)
```

对应的审阅请求及成对响应示例见 [虚构交互样例](../examples/scope_review/exchange.json)。开发和验收使用各自的材料列表。[开发样例](../examples/scope_review/development.json) · [独立虚构评测样例](../examples/scope_review/evaluation.json)

## 跨轮定位约定

用 `relink_evidence(previous_text, current_text, evidence)` 将上一轮留下的真实文本引文定位到下一份稿件。相同段落移到别处时会定位到新位置。原引文仍在但段内内容改变，或通过现有对齐找到改写后的候选段，均返回 `changed_context`。重复段落、引文歧义和文本消失由 `ambiguous` 或 `missing` 提示。引文位置验证失败会报错。

界面应分别定位上一轮用到的结论、限定条件和支撑材料。作者更改要求，或任一关键位置的上下文改变时，呈现上次理由并重新核对。即使每个引文都能定位，作者的决定仍与当时的稿件版本绑定。测试覆盖段落搬移、局部改写、重复证据和缺失证据。

## 材料和评测

开发稿有三组虚构案例。人工走查时可按下表逐条问作者，结合 JSON 中的 `manual_question` 和原文定位展示。

| 案例 | 读给作者的两版证据 | 人工核对点 |
| --- | --- | --- |
| `dev-widened` | 原稿的「在三家试点酒店」与改稿的「在所有酒店」 | 访谈只覆盖试点酒店，作者要保留限定还是补材料 |
| `dev-moved` | 原稿的「在人工模拟数据上」与改稿前置的「本段的结论只适用于人工模拟数据」 | 同一段落的结论仍受限定，核对两处是否指向同一主张 |
| `dev-allowed-without-data` | 原稿的「在这座水库的演练中」与改稿的「在全流域的水库中」 | 作者允许有多库观测时扩大范围，当前文稿只有单库记录 |

第一套评测用另外五组虚构材料，另含支持结论扩大的新增样本和主动缩小结论。修订 Skill 后，在相同五组上复算，并用[三组新样例](../examples/scope_review/confirmation.json)独立复验。每处判断绑定到 `review_units()`。先用 `--manifest` 固定材料、稿件、作者要求和请求的指纹。在本地已确认的私有位置保存真正的稿件、作者要求、双人参考答案和原始模型响应。正式评测仍依赖作者确认的独立真实稿件及人工复核。这里的虚构材料用于检查接口和初步发现失败案例。

```sh
mkdir -p data/private/scope-review
uv run --locked --extra web python -m experiments.evaluate_scope_review \
  examples/scope_review/evaluation.json --manifest data/private/scope-review/manifest.json
uv run --locked --extra web python -m experiments.evaluate_scope_review \
  examples/scope_review/evaluation.json --codex-model gpt-6-luna --effort low \
  --raw-out data/private/scope-review/luna-low-raw.json --out data/private/scope-review/luna-low-score.json
```

第二条命令在本机选择并使用已登录 Codex CLI 账户，发送案例正文和作者要求。显式添加 `--run03 路径` 可以同时计算旧模型在相同单元的推送情况，权重和基线结果都留在本地。评测保留有效定位数、范围分类正确数、范围加证据状态同时正确数、漏检、误报、错误判定证据充分和待核对项。人工查看原始回复中的论证解释和提问是否合理。复算可用 `--score 已有答卷.json --out 新分数文件.json`，无需再次调用模型。

## 2026-11-23 的实测记录

各轮真实调用均用本机 Codex CLI 的 `gpt-6-luna`，推理档 `low`。参考判断来自本仓库的虚构材料。旧模型采用签名桌面包内的冻结 run03 文件，SHA-256 为 `568317b5e0b707c359f15dfe999a2c56a9bdf41e5674c3630b7aef198a1968bd`。run03 的推送表示建议人工审阅。它没有对应的范围与证据状态输出。

| 运行 | 审阅单元 | 有效定位 | 范围和证据状态均符合参考 | 漏掉范围扩大或缩小 | 把其他项误报为扩大或缩小 | run03 推送 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Skill v1，五组初测 | 6 | 4 | 2 | 1 | 1 | 4 |
| Skill v2，同五组复验 | 6 | 5 | 4 | 1 | 1 | 4 |
| Skill v2，三组新样例 | 6 | 5 | 5 | 0 | 0 | 3 |

v1 在稿内证据已补足的案例省略了需要作者回答的问题。v2 修正这项指令，复验时该案例得到两条有效结果。模型在同一批复验中仍把互不重叠的两个时段误报为扩大，并在 4 月结论缩小案使用了不适用的证据状态，后一条进入待核对。三组新样例中，一个仅增加支撑材料的单元被模型当成另一条结论，因缺少原稿主张引文而进入待核对。兼容了完整 JSON 后误附的单个分号，再用原始响应复算。与旧版相比，这个处理使一条可定位、其余字段均正确的响应恢复有效。版本 v1 可从本地提交 `eb5ec6c` 复现。

本地全量测试 1,088 项通过，9 项因缺少可选 NLI/BGE 权重跳过。测试覆盖连接错误与重试、迟到结果、引文校验、跨轮定位和旧进度恢复。独立 wheel 可读取新 Skill 并生成请求，原 CLI、导出和 MCP 当前及旧版协议均实测通过。macOS App 已构建并核验签名，包内 Skill 与源码一致，隔离启动时无需 run03。工作台界面的审阅入口与版本状态由 Tim 接入，真人试用和正式双人参考判断仍需团队共同完成。
