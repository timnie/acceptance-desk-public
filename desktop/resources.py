"""校验维护者显式启用的 run03 历史模型。"""

from hashlib import sha256
from pathlib import Path

MODEL_SHA256 = "568317b5e0b707c359f15dfe999a2c56a9bdf41e5674c3630b7aef198a1968bd"


def verify_model(path: Path) -> Path:
    if not path.is_file():
        raise ValueError("找不到指定的 run03 历史模型，请检查模型路径。")
    if sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("run03 历史模型校验失败，请检查冻结模型文件。")
    return path
