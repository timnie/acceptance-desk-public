"""按两版全文中的出处差异补充已饱和的逐句雷达。"""

import re
import unicodedata

from acceptance_desk.lexicon import find_citations, load_lexicon
from acceptance_desk.text import normalize


_LOCATOR = re.compile(
    r"[（(](?P<locator>[A-Z][a-zA-Z.]{1,10}\s+(?:[A-Z]\s*\d|\d+\s*[:：])"
    r"[^()（）\r\n]{0,32})[）)]"
)


def _references(text, lexicon):
    refs = find_citations(text, lexicon)
    refs.extend((m.start("locator"), m.end("locator"), m["locator"]) for m in _LOCATOR.finditer(text))
    return [(start, end, value, "".join(unicodedata.normalize("NFKC", value).casefold().split()))
            for start, end, value in refs]


def reference_evidence(original, revised, changes):
    """定位另一版全文没有的文献标记，保留搬移过的同一出处。"""
    lexicon = load_lexicon()
    sources = {"original": _references(normalize(original), lexicon), "revised": _references(normalize(revised), lexicon)}
    unmatched = {}
    for side, other_side in (("original", "revised"), ("revised", "original")):
        other = {key for _, _, _, key in sources[other_side]}
        unmatched[side] = [(start, end, text) for start, end, text, key in sources[side] if key not in other]
    result = {}
    for change in changes:
        evidence = tuple(dict.fromkeys(
            (side, text) for side, spans in unmatched.items() for start, end, text in spans
            if change[side]["text"] and change[side]["start"] < end and change[side]["end"] > start
        ))
        if evidence:
            result[change["id"]] = evidence
    return result
