# Research tools

These scripts support sampling, annotation, model comparison, and evaluation. Each CLI accepts `--help`. Use [the fictional manuscripts](../examples/) for public examples. Research manuscripts, labels, and participant records are outside the source distribution.

## 独立对照工具

普通 diff 可以作为工作台的对照。以下命令使用仓库里的虚构样稿，输出一份本地 HTML 和一份空白交叉分配表。

```bash
mkdir -p data/private/user-study
uv run --locked python experiments/user_study.py baseline examples/original.txt examples/revised.txt --material-id A --out data/private/user-study/A_diff.html
uv run --locked python experiments/user_study.py schedule --out data/private/user-study/records.csv
```

浏览器打开本地 HTML 即可使用普通左右 diff。它展示全文、行号及字词增删，不运行 audit、分类器或规则，不加载远程资源，不含参考标签。原文、改文和材料标题均转义，Markdown 按源码展示。正文哈希写在 HTML 注释中，便于核对验收台使用同版本文本（只统一换行）。基线页不负责计时、提交或保存参与者答案。
