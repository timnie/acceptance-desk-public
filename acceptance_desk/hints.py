"""原稿已有且在改稿中保留的套话/防御词表命中；独立于 AuditReport。"""

from __future__ import annotations

from bisect import bisect_right
from difflib import SequenceMatcher

from ._derived import checked_source
from .decisions import ReviewDataError
from .lexicon import find_hits, load_lexicon
from .text import split_sentences

HINT_CATEGORIES = {"slop": "套话词表", "defensive": "防御表述词表"}


def original_hints(report: dict, original: str, revised: str, *, lexicon_path: str | None = None) -> list[dict]:
    """只保留完整落在 equal 字符区间的原稿命中。位置基于统一换行后的原稿。

    同一个词即使在其他位置出现，也不靠字符串查找回填；按本次对齐被删去或改写的命中不算保留。
    不修改 report，不写入风险、进度或特征。传入的词表版本必须与报告一致。
    """
    original, revised, segments = checked_source(report, original, revised)
    lexicon = load_lexicon(lexicon_path)
    if lexicon.version != report["run"]["lexicon_version"]:
        raise ReviewDataError("词表版本与报告不一致，请提供生成该报告的词表")
    # (原稿起止, 改稿起点, 修改编号)，每个区间都是两侧实际相同的字符。
    retained = []
    for segment in segments:
        if segment.o_span is None or segment.r_span is None:
            continue
        os, oe = segment.o_span
        rs, re = segment.r_span
        cid = segment.change["id"] if segment.change else None
        if original[os:oe] == revised[rs:re]:
            retained.append((os, oe, rs, cid))
            continue
        for block in SequenceMatcher(None, original[os:oe], revised[rs:re], autojunk=False).get_matching_blocks():
            if block.size:
                retained.append((os + block.a, os + block.a + block.size, rs + block.b, cid))
    starts = [s for s, *_ in retained]
    sentences = split_sentences(original)
    sentence_starts = [sentence.start for sentence in sentences]
    result = []
    for hit in find_hits(original, lexicon):
        if hit["category"] not in HINT_CATEGORIES:
            continue
        index = bisect_right(starts, hit["start"]) - 1
        if index < 0:
            continue
        start, end, revised_start, cid = retained[index]
        if hit["end"] > end:
            continue
        si = bisect_right(sentence_starts, hit["start"]) - 1
        sentence = sentences[si] if si >= 0 else None
        if sentence is None or hit["end"] > sentence.end:
            continue
        mapped_start = revised_start + hit["start"] - start
        result.append({"category": hit["category"], "category_zh": HINT_CATEGORIES[hit["category"]],
                       "term": hit["term"], "text": hit["text"], "start": hit["start"], "end": hit["end"],
                       "revised_start": mapped_start, "revised_end": mapped_start + len(hit["text"]),
                       "change_id": cid,
                       "sentence": {"text": sentence.text, "start": sentence.start, "end": sentence.end,
                                    "paragraph": sentence.paragraph},
                       "message": f"原稿已有：“{hit['text']}”（{HINT_CATEGORIES[hit['category']]}）"})
    width = max(3, len(str(len(result))))
    return [{"id": f"h{k:0{width}d}", **hit} for k, hit in enumerate(result, 1)]
