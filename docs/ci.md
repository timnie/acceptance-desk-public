# 持续集成

## 设计与实施

目标：每个 PR 和 main 提交都有可重复的测试与安装包检查。工作流使用 Python 3.10 和 3.12，按 uv.lock 安装开发、工作台（web）与 MCP 依赖。

1. 安装锁定的依赖，运行 `tests/`。工作台状态、视图和历史实验的虚构材料演练都在测试套件里。
2. 构建源码包和 wheel。
3. 新建干净环境安装 wheel，从源码目录之外执行公开示例，核对词表、命令入口和报告格式。
4. 推送 PR，核对两个 Python 版本的 GitHub 实际运行结果。

工作流文件：`.github/workflows/ci.yml`。触发事件为所有目标分支的 PR、main 推送，以及 Actions 页面手动运行。新提交会取消同分支的旧运行；单个任务限时 15 分钟。Action 固定到完整提交号，仓库令牌只有读取权限。

## 检查范围

普通 CI 使用公开自编示例，按现有 fixture 跳过需要本地权重的用例；`-ra` 会列出跳过原因。模型文件核验、模型缺失处理等轻量回归照常运行。CI 不接触论文、盲标答案或本地模型缓存。

真实模型推理由本地离线检查执行：

```bash
ACCEPTANCE_DESK_REQUIRE_MODELS=1 uv run --locked --extra models pytest tests/test_models.py
```

## 本地复现

```bash
uv sync --locked --python 3.10 --group dev --extra web --extra mcp
uv run --locked --group dev --extra web --extra mcp pytest -q -ra
uv build
uv venv --python 3.10 /tmp/acceptance-wheel-check
uv pip install --python /tmp/acceptance-wheel-check/bin/python dist/*.whl
/tmp/acceptance-wheel-check/bin/python .github/scripts/verify_install.py "$PWD"
```

将 3.10 换为 3.12 可核对另一个环境。修改依赖时同步提交 uv.lock；检查失败时在 PR 的 Checks 中展开失败步骤及日志。

合并本 PR 后，现有开放 PR 更新分支或重新打开即可触发检查。main 的必要检查设置由团队在确认首次运行后配置。自动发布在版本与发布渠道确定后加入。
