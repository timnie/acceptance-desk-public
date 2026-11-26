"""中文提交是独立的撤销单元；普通连续键入仍可合并。也供浏览器端 Python 编译使用。"""


def history_boundary(event: str, previous: str, selected: bool) -> str | None:
    if event == 'input.type.compose.start':
        return 'before'
    if event.startswith('input.type.compose'):
        return None  # 候选变化及最终提交属于同一次组合输入。
    if event in ('input.paste', 'input.drop'):
        return 'full'
    if selected and (event.startswith('input') or event.startswith('delete')):
        return 'full'
    if previous.startswith('input.type.compose'):
        return 'before'
    return None
