"""Deterministic text features. Lexical signals only: they nominate evidence, they do not understand it."""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

STOPWORDS = frozenset("""
a an the is are was were be been being am to of in on at by for with and or but as it its this that these
those from into than then there here we our you your they their i me my he she his her them us will would
shall should can could may might must do does did has have had so such also just very about over under
after before again via per if when while which who whom what where how all any each every some only own
same both more most other through not no never none nor always probably likely maybe perhaps seems think
""".split())
ZH_STOP = "的了是在和与及也都就而被把对为这那有我你他她它们个"
ZH_NEGATION = "不没未无非别勿"

NEGATION = re.compile(
    r"\b(?:not|no|never|none|nobody|nothing|neither|nor|without|cannot|no longer|\w+n't|"
    r"skip(?:s|ped|ping)?|bypass(?:es|ed|ing)?)\b|[" + ZH_NEGATION + "]", re.I)
ABSOLUTE = re.compile(
    r"\b(?:always|never|all|every|everyone|everything|nobody|guaranteed|definitely|certainly|impossible)\b|"
    r"总是|从不|从来|永远|所有|一切|一定|绝对|肯定|全部|任何|必然", re.I)
RELATIVE_TIME = re.compile(
    r"\b(?:today|tonight|yesterday|tomorrow|currently|right now|recently|lately|soon|"
    r"(?:next|last|this) (?:week|month|year|sprint|quarter|release))\b|"
    r"今天|昨天|明天|现在|目前|最近|近期|下周|上周|本周|这周|下个月|上个月|今年|明年|去年|马上|即将", re.I)
HEDGE = re.compile(
    r"\b(?:probably|likely|maybe|perhaps|might|seems?|apparently|presumably|i think|we think|assume[sd]?)\b|"
    r"可能|也许|大概|似乎|好像|估计|应该是|猜测", re.I)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
ZH_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*|\d+(?:[.:/-]\d+)*|[㐀-鿿]+")
SUFFIXES = ("ations", "ation", "ments", "ment", "ions", "ion", "ency", "ence", "ings", "ing",
            "ies", "ied", "ed", "es", "al", "ly", "s", "e")

# Pairs of terms that cannot both describe the same state. English terms are stemmed on load.
ANTONYMS_EN = [("approve", "reject"), ("approve", "deny"), ("accept", "reject"), ("accept", "decline"),
               ("enable", "disable"), ("allow", "block"), ("allow", "forbid"), ("pass", "fail"),
               ("succeed", "fail"), ("true", "false"), ("add", "remove"), ("start", "stop"),
               ("open", "close"), ("include", "exclude"), ("increase", "decrease"), ("public", "private"),
               ("required", "optional"), ("supported", "deprecated")]
ANTONYMS_ZH = [("批准", "拒绝"), ("批准", "否决"), ("通过", "否决"), ("同意", "拒绝"), ("启用", "禁用"),
               ("开启", "关闭"), ("允许", "禁止"), ("成功", "失败"), ("增加", "减少"), ("支持", "弃用")]


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


ANTONYM_STEMS = [(stem(a), stem(b)) for a, b in ANTONYMS_EN]


def _cjk_tokens(run: str) -> Iterator[str]:
    for piece in re.split("[" + ZH_STOP + ZH_NEGATION + "]+", run):
        if len(piece) == 1:
            yield piece
        for i in range(len(piece) - 1):
            yield piece[i:i + 2]


def features(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (content tokens, numeric tokens)."""
    words, numbers = set(), set()
    for match in TOKEN.finditer(text):
        token = match.group()
        if token[0].isdigit():
            numbers.add(token)
        elif "㐀" <= token[0] <= "鿿":
            words.update(_cjk_tokens(token))
        elif len(token) == 1 and token.isupper():
            words.add(token)  # identifiers such as "Feature A"; lowercase "a" is a stopword
        else:
            lower = token.lower().removesuffix("'s")
            if lower not in STOPWORDS and "'" not in lower:
                words.add(stem(lower))
    return frozenset(words), frozenset(numbers)


def has_negation(text: str) -> bool:
    return bool(NEGATION.search(text))


def opposing_terms(claim: str, passage: str) -> tuple[str, str] | None:
    """Return (claim term, passage term) if they name mutually exclusive states."""
    claim_words, _ = features(claim)
    passage_words, _ = features(passage)
    for a, b in ANTONYM_STEMS:
        for x, y in ((a, b), (b, a)):
            if x in claim_words and y in passage_words and x not in passage_words:
                return x, y
    for a, b in ANTONYMS_ZH:
        for x, y in ((a, b), (b, a)):
            if x in claim and y in passage and x not in passage:
                return x, y
    return None


def explicit_dates(text: str) -> list[date]:
    found = []
    for pattern in (ISO_DATE, ZH_DATE):
        for y, m, d in pattern.findall(text):
            try:
                found.append(date(int(y), int(m), int(d)))
            except ValueError:
                pass
    return found


_LEAD = re.compile(r"^(?:\s*(?:#{1,6}|>|[-*+]|\d+[.)]|\[[ xX]\])\s+)+")
_BOUNDARY = re.compile(r"[。！？；]|[.!?;](?=\s|$)")


def sentence_spans(text: str) -> Iterator[tuple[int, int]]:
    """Yield [start, end) character spans of sentences, with markdown list/heading markers trimmed."""
    for line in re.finditer(r"[^\n]+", text):
        base, body, pos = line.start(), line.group(), 0
        cuts = [m.end() for m in _BOUNDARY.finditer(body)] + [len(body)]
        for cut in cuts:
            segment = body[pos:cut]
            lead = _LEAD.match(segment)
            offset = lead.end() if lead else len(segment) - len(segment.lstrip())
            stripped = segment[offset:].rstrip()
            if re.search(r"\w", stripped):
                yield base + pos + offset, base + pos + offset + len(stripped)
            pos = cut
