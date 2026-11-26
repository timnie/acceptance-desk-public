"""实验输出共用的独占写入：先准备所有内容，路径冲突或写入失败时撤回本批新文件。"""

import hashlib
from contextlib import ExitStack
from pathlib import Path


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new_files(outputs: list[tuple[Path, str]]) -> None:
    paths = [path.resolve() for path, _ in outputs]
    if len(set(paths)) != len(paths):
        raise ValueError("本批输出路径重复，不能把两种结果写到同一文件")
    created = []
    try:
        with ExitStack() as stack:
            handles = []
            for path, _ in outputs:
                handle = stack.enter_context(path.open(
                    "x", encoding="utf-8-sig" if path.suffix.lower() == ".csv" else "utf-8", newline=""))
                stat = path.lstat()
                created.append((path, stat.st_dev, stat.st_ino))
                handles.append(handle)
            for handle, (_, content) in zip(handles, outputs):
                handle.write(content)
    except BaseException:
        for path, device, inode in created:
            try:
                stat = path.lstat()
                if (stat.st_dev, stat.st_ino) == (device, inode):
                    path.unlink()
            except FileNotFoundError:
                pass
        raise
