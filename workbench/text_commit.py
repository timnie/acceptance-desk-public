"""不把输入法尚未提交的候选内容交给文稿保存逻辑。"""


class CommitGate:
    def __init__(self, value: str) -> None:
        self.committed = self.raw = value
        self.composing = self.pending = False

    def begin(self) -> None:
        self.composing, self.pending = True, False

    def end(self) -> None:
        self.composing, self.pending = False, True

    def update(self, value: str) -> str | None:
        self.raw = value
        return None if self.composing or self.pending else self.flush()

    def flush(self) -> str | None:
        if self.composing:
            return None
        self.pending = False
        if self.raw == self.committed:
            return None
        self.committed = self.raw
        return self.committed
