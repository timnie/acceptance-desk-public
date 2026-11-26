from workbench.text_commit import CommitGate


def test_only_committed_chinese_is_published():
    gate = CommitGate("原句")
    gate.begin()
    assert gate.update("原句nihao") is None
    gate.end()
    assert gate.update("原句你好") is None
    assert gate.flush() == "原句你好"
    assert gate.flush() is None


def test_final_input_can_arrive_before_composition_end():
    gate = CommitGate("")
    gate.begin()
    gate.update("nihao")
    gate.update("你好")
    gate.end()
    assert gate.flush() == "你好"


def test_cancel_and_back_to_back_compositions_do_not_publish_candidates():
    gate = CommitGate("原句")
    gate.begin()
    gate.update("原句ni")
    assert gate.flush() is None
    gate.update("原句")
    gate.end()
    assert gate.flush() is None
    gate.begin()
    gate.update("原句你好")
    gate.end()
    assert gate.flush() == "原句你好"
    gate.begin()
    assert gate.update("原句你好shijie") is None
    gate.end()
    gate.update("原句你好世界")
    assert gate.flush() == "原句你好世界"


def test_plain_typing_paste_and_undo_publish_normally():
    gate = CommitGate("")
    assert gate.update("abc") == "abc"
    assert gate.update("abc你好") == "abc你好"
    assert gate.update("abc") == "abc"
    assert gate.update("abc") is None
