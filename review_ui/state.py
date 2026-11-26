"""审阅状态：上传解码、回填 decision、保存 / 恢复审阅记录（schema v0.1 §4.2）。"""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime

from acceptance_desk._schema import SCHEMA_VERSION
from acceptance_desk.decisions import USER_STATUSES, decision_errors, is_trained

STATUS_ZH = {"pending": "待定", "accepted": "已接受", "rejected": "已退回", "edited": "已编辑", "auto_passed": "自动通过"}
_SIDE_KEYS = ("start", "end", "text")


class ReviewError(ValueError):
    """给用户看的中文错误（页面层：上传、导入）。决定本身的合法性由核心 decision_errors 判断。"""


# ---------------------------------------------------------------- 输入

FORMATS = {"txt": "纯文本（.txt）", "md": "Markdown（.md）"}
_SUFFIX_FORMAT = {".txt": "txt", ".text": "txt", ".md": "md", ".markdown": "md"}


def format_of(filename: str) -> str:
    """按后缀判断源格式：.md / .markdown 为 md，.txt 为 txt；其他后缀报错。"""
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in _SUFFIX_FORMAT:
        raise ReviewError(f"「{filename}」不是 .txt 或 .md 文件。PDF、DOCX 请先另存为纯文本或 Markdown。")
    return _SUFFIX_FORMAT[suffix]


def read_upload(data: bytes, filename: str, side_zh: str = "文件") -> tuple[str, str]:
    """读取上传的 .txt / .md：返回 (源文本, 格式)。Markdown 按源码原样保留，不渲染、不删语法。"""
    fmt = format_of(filename)
    return decode_upload(data, f"{side_zh}「{filename}」"), fmt


def decode_upload(data: bytes, name: str = "文件") -> str:
    """把上传的文本解码为 str；接受带 BOM 的 UTF-8，其他编码报中文错误。"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ReviewError(f"{name} 不是 UTF-8 编码（第 {e.start} 字节处无法解码）。请另存为 UTF-8 文本后重新上传。") from e


def new_review(report: dict) -> dict:
    """从 audit() 结果得到一份可修改的审阅状态（深拷贝，不改缓存里的报告）。"""
    return copy.deepcopy(report)


# ---------------------------------------------------------------- 决定

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_change(review: dict, change_id: str) -> dict:
    for c in review["changes"]:
        if c["id"] == change_id:
            return c
    raise KeyError(change_id)


def set_decision(review: dict, change_id: str, status: str, final_text: str | None = None,
                 *, now: str | None = None) -> dict:
    """按 §4.2 回填一条 decision，保留备注。返回该 decision。"""
    if status not in USER_STATUSES:
        raise ValueError(f"页面只能设置 {USER_STATUSES}，收到 {status!r}")
    c = find_change(review, change_id)
    dec = c["decision"]
    if status == "pending":
        dec.update(status="pending", final_text=None, decided_by=None, decided_at=None)
        return dec
    if status == "accepted":
        text = c["revised"]["text"]
    elif status == "rejected":
        text = c["original"]["text"]
    else:
        if not isinstance(final_text, str):
            raise ValueError("编辑后采用需要给出文本")
        text = final_text
    dec.update(status=status, final_text=text, decided_by="user", decided_at=now or _now())
    return dec


def set_note(review: dict, change_id: str, note: str) -> None:
    find_change(review, change_id)["decision"]["note"] = note or ""


# ---------------------------------------------------------------- 撤销与批量

UNDO_LIMIT = 50


def snapshot(review: dict, ids, *, label: str, cur: str | None) -> dict:
    """操作前的快照：受影响各组的完整 decision（状态、最终文本、决定人、时间、备注）与当时选中的组。"""
    ids = list(dict.fromkeys(ids))
    return {"label": label, "cur": cur,
            "decisions": {cid: copy.deepcopy(find_change(review, cid)["decision"]) for cid in ids}}


def push_undo(stack: list, entry: dict) -> None:
    stack.append(entry)
    del stack[:-UNDO_LIMIT]


def undo(review: dict, stack: list) -> dict | None:
    """撤销最近一次操作：恢复受影响各组的 decision，返回这条记录（含应恢复的选中组）。没有可撤销的返回 None。"""
    if not stack:
        return None
    entry = stack.pop()
    for cid, dec in entry["decisions"].items():
        find_change(review, cid)["decision"] = copy.deepcopy(dec)
    return entry


def batch_targets(review: dict, visible: list[str]) -> list[str]:
    """批量操作的对象：当前筛选范围内仍待定的组（不碰已决定或已编辑的组）。"""
    pending = {c["id"] for c in review["changes"] if c["decision"]["status"] == "pending"}
    return [cid for cid in visible if cid in pending]


def batch_decide(review: dict, ids: list[str], status: str, *, now: str | None = None) -> list[str]:
    """把 ids 中仍待定的组设为 accepted / rejected；返回实际改动的编号。"""
    if status not in ("accepted", "rejected"):
        raise ValueError("批量操作只支持采用改句或保留原句")
    done = []
    for cid in ids:
        if find_change(review, cid)["decision"]["status"] == "pending":
            set_decision(review, cid, status, now=now)
            done.append(cid)
    return done


def describe_ids(ids: list[str], limit: int = 12) -> str:
    """“第 1、3、4 处”这样的范围说明。"""
    nums = [str(int(c[1:])) if c[1:].isdigit() else c for c in ids]
    head = "、".join(nums[:limit])
    return f"第 {head}{' 等' if len(nums) > limit else ''} 处" if nums else "（无）"


def progress(review: dict) -> dict:
    """总体和两组的决定计数。"""
    out = {"total": len(review["changes"]), "by_status": Counter(), "flagged_pending": 0, "other_pending": 0}
    for c in review["changes"]:
        st = c["decision"]["status"]
        out["by_status"][st] += 1
        if st == "pending":
            out["flagged_pending" if c["review"]["flagged"] else "other_pending"] += 1
    out["decided"] = out["total"] - out["by_status"]["pending"]
    out["by_status"] = dict(out["by_status"])
    return out


# ---------------------------------------------------------------- 导航

FILTERS = ("flagged", "other", "pending", "all")


def visible_ids(review: dict, name: str) -> list[str]:
    """筛选：重点 / 其余 / 待定 / 全部，保持原文顺序。"""
    keep = {
        "flagged": lambda c: c["review"]["flagged"],
        "other": lambda c: not c["review"]["flagged"],
        "pending": lambda c: c["decision"]["status"] == "pending",
        "all": lambda c: True,
    }[name]
    return [c["id"] for c in review["changes"] if keep(c)]


def step(order: list[str], visible: list[str], cur: str | None, delta: int) -> str | None:
    """在筛选结果里找上一处（-1）/ 下一处（+1）。当前条目不在筛选里（如刚决定、离开“待定”）时，
    按原文顺序找它之后 / 之前最近的可见条目。没有则返回 None。"""
    if not visible:
        return None
    if cur not in order:
        return visible[0]
    pos = {cid: k for k, cid in enumerate(order)}
    here = pos[cur]
    cands = [v for v in visible if (pos[v] > here if delta > 0 else pos[v] < here)]
    if not cands:
        return None
    return cands[0] if delta > 0 else cands[-1]


# ---------------------------------------------------------------- 导出文件名

_BAD_NAME_CHARS = '\\/:*?"<>|\n\r\t'


def export_names(title: str, ext: str, draft: bool, avoid: list[str | None] = ()) -> dict[str, str]:
    """终稿与审阅记录的下载文件名；与上传的原稿 / 改稿同名时加“_导出”，避免误覆盖。"""
    if ext not in FORMATS:
        raise ValueError(f"终稿格式只能是 {tuple(FORMATS)}")
    base = "".join("_" if ch in _BAD_NAME_CHARS else ch for ch in (title or "文稿")).strip(" .") or "文稿"
    tag = "草稿" if draft else "终稿"
    names = {"final": f"{base}_{tag}.{ext}", "record": f"{base}_审阅记录{'_草稿' if draft else ''}.md"}
    taken = {n.lower() for n in avoid if n}
    for k, n in names.items():
        while n.lower() in taken:
            stem, _, suf = n.rpartition(".")
            n = f"{stem}_导出.{suf}"
        names[k] = n
    return names


# ---------------------------------------------------------------- 保存 / 恢复

def review_snapshot(review: dict, *, now: str | None = None) -> dict:
    """完整 AuditReport（decision 已回填）+ 附加的 review_meta。"""
    out = copy.deepcopy(review)
    p = progress(review)
    out["review_meta"] = {"saved_at": now or _now(), "decided": p["decided"], "total": p["total"],
                          "by_status": p["by_status"]}
    return out


def export_review(review: dict, *, now: str | None = None) -> str:
    return json.dumps(review_snapshot(review, now=now), ensure_ascii=False, indent=2)


def _same(a, b) -> bool:
    """类型和值都相同：避免 True == 1、False == 0 这类混过位置比较。"""
    return type(a) is type(b) and a == b


def parse_progress_file(text: str | bytes) -> dict:
    """检查进度文件的编码、结构和版本。匹配两版文稿时再验证每一处决定。"""
    if isinstance(text, bytes):
        text = decode_upload(text, "进度文件")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ReviewError(f"进度文件无法读取（第 {e.lineno} 行第 {e.colno} 列）。请选择从验收台下载的原始 .json 文件。") from e
    except (ValueError, RecursionError) as e:
        raise ReviewError("进度文件无法读取。请选择从验收台下载的原始 .json 文件。") from e
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list) or not isinstance(data.get("document"), dict):
        raise ReviewError("这份文件没有可恢复的审阅记录。请通过「导出 → 下载进度文件」取得正确的 .json 文件。")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ReviewError(f"这份进度文件的版本和当前验收台不兼容。请用导出它的版本恢复（文件版本 {data.get('schema_version')!r}，当前支持 {SCHEMA_VERSION}）。")
    if any(not isinstance(data["document"].get(f"{side}_sha256"), str) for side in ("original", "revised")):
        raise ReviewError("进度文件缺少两版文稿的校验信息。请重新下载进度文件。")
    return data


def load_review(text: str | bytes, current: dict) -> dict:
    """校验导入的审阅记录与当前审计是否同一份文稿、同一组条目，通过则返回新的审阅状态。"""
    data = parse_progress_file(text)

    cur_doc, doc = current["document"], data["document"]
    if (doc["original_sha256"] == cur_doc["revised_sha256"] and doc["revised_sha256"] == cur_doc["original_sha256"]
            and cur_doc["original_sha256"] != cur_doc["revised_sha256"]):
        raise ReviewError("原稿和改稿放反了。请把两份稿件调换位置，再恢复审阅。")
    mismatched = [label for side, label in (("original", "原稿"), ("revised", "改稿"))
                  if doc[f"{side}_sha256"] != cur_doc[f"{side}_sha256"]]
    if mismatched:
        raise ReviewError(f"{'和'.join(mismatched)}与保存进度时的内容不一致。请使用当时的文件，包括原有的空格和空行。")

    cur_changes, changes = current["changes"], data["changes"]
    if len(changes) != len(cur_changes):
        raise ReviewError(f"进度文件记录了 {len(changes)} 处改动，当前版本检查出 {len(cur_changes)} 处，无法对应。请用导出进度时的版本重试。")
    errs: list[str] = []
    for c, cur in zip(changes, cur_changes):
        if not isinstance(c, dict):
            errs.append("存在不是对象的条目")
            continue
        cid = cur["id"]
        if not _same(c.get("id"), cid) or c.get("alignment") != cur["alignment"]:
            errs.append(f"{cid}: 编号或对齐方式与当前检查结果不一致（记录里为 {c.get('id')!r}）")
            continue
        for side in ("original", "revised"):
            s = c.get(side)
            if not isinstance(s, dict) or not all(_same(s.get(k), cur[side][k]) for k in _SIDE_KEYS):
                errs.append(f"{cid}: {'原句' if side == 'original' else '改句'}的位置或文本与当前文稿不一致")
        # 决定是否合法：与合并终稿共用核心规则（含未训练时不许“自动通过”、决定人与时间）
        errs.extend(decision_errors({**cur, "decision": c.get("decision")}, trained=is_trained(current),
                                    created_at=current["run"]["created_at"]))
    if errs:
        shown = "；".join(errs[:5]) + (f"；另有 {len(errs) - 5} 处问题" if len(errs) > 5 else "")
        raise ReviewError("审阅记录与当前检查结果不符，未导入：" + shown)

    review = new_review(current)
    for c, dst in zip(changes, review["changes"]):
        d = c["decision"]
        dst["decision"] = {"status": d["status"], "final_text": d.get("final_text"),
                           "decided_by": d.get("decided_by"), "decided_at": d.get("decided_at"),
                           "note": d.get("note", "")}
    return review
