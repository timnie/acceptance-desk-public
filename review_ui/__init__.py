"""工作台共用的决定状态与文字比对：state.py 回填 decision、撤销、批量、导入导出进度；render.py 生成转义后的差异 HTML。

不依赖界面框架，只读写报告里的 decision；审计只通过 acceptance_desk.audit()。旧 Streamlit 审阅页已删除（2026-11-12）。
"""
