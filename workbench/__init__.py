"""浏览器文稿工作台（NiceGUI）。界面在 app.py，不依赖界面框架的状态与显示整理在 session.py、view.py。

运行：uv run --extra web python -m workbench
复用 acceptance_desk 的审计、合并、导出，以及 review_ui 的决定、撤销和文字比对；不改它们。
"""
