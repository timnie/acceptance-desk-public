# 按审阅单元试标和测量

这是 #15 的工具适配，不改变 schema v0.1 或标签；原始报告与自检人工映射仍使用 `c...` 修改编号。单元表使用自己的编号，不把一条单元标签复制到所有成员记录。

## 输入和抽样

先保存同一轮的审计 JSON（下例路径仅为占位，不能与既有文件重名），再导出自动归组候选：

```bash
uv run --locked acceptance-desk data/raw/p01.txt data/revised/p01_r03.txt --out data/labels/p01_report.json
uv run --locked python experiments/pilot.py units data/labels/p01_report.json --original data/raw/p01.txt --revised data/revised/p01_r03.txt --out data/labels/p01_units_candidate.json
```

单元 JSON 的 `report_sha256` 绑定报告，`units` 是完整分区，例如 `[{"id":"u001","change_ids":["c001","c002"]}, ...]`。每个原始修改必须恰好出现一次，单元及其成员按原报告顺序排列。人工核对自动候选，或将已有人工分区整理成这个格式，另存为定稿分区。不能只传抽出的 30 个单元，也不能将现有 98 个人工单元悄悄换成 111 个自动单元。

```bash
uv run --locked python experiments/pilot.py sheet data/raw/p01.txt data/revised/p01_r03.txt --report data/labels/p01_report.json --unit-map data/labels/p01_units_final.json --name p01_content_round2 --out-dir data/labels
```

输出全新的 A/B 空白表、单元钥匙和原始报告；任何目标已存在时拒绝整批覆盖。它按单元分层随机抽样，随机种子、完整分区、原始记录总数与报告指纹均留在钥匙。编号、原文、位置和上下文不应由标注员改写。非相邻成员间如夹有其他单元文字，表中用 `[…其他单元…]` 标明；位置是包络区间，不表示表中文字为连续全文切片。

## 测量

完成独立内容标注后：

```bash
uv run --locked python experiments/pilot.py measure data/labels/p01_content_round2_report.json data/labels/p01_content_round2_pilot_key.json data/labels/p01_content_round2_pilot_A.csv data/labels/p01_content_round2_pilot_B.csv --original data/raw/p01.txt --revised data/revised/p01_r03.txt
```

带三次自检时追加 `--selfcheck` 与原有 `--mapping`。自检先按原始记录唯一定位或读取人工确认，再映射到单元；同一单元命中多条成员只计一次。命中一个成员不表示找全该单元的全部风险。输出明确标注测量单位为审阅单元；不能把它与逐记录召回率混报。

单元中任一原始记录有候选重点则该单元被推送，这个聚合规则固定记录于本工具说明。全章比例与筛选指标按单元分层加权；自检样本表仍是同批单元内的原始计数。手选样本或旧的非标准钥匙不能直接作为本工具的分层随机样本；需要保留原抽样证据并核对转换，不能靠补一个字段使设计成立。

单元分区完整覆盖报告编号之外，还会重走共享导出核心的全文覆盖检查，拒绝报告漏条、错序和重复。历史报告若与当前对齐不同会报错，不自动改写或重做真实标注。

文风细分表不进入这条内容测量链路。口径未校准的表、讨论后的共识表不能冒充新一轮独立标注。逐记录与单元测量现在都要求全文、报告指纹、随机设计、合法种子、句子规模，并重放样本。
