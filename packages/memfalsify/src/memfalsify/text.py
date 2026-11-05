"""Deterministic text features. Lexical signals only: they nominate evidence, they do not understand it.

Full-width forms (``ＡＰＩ``, ``１２０``, ``，``) are read as their ASCII counterparts. Text that contains CJK
ideographs gets more, none of which applies to text without them: Chinese numerals, ``万``/``亿``/``千``
multipliers, ``百分之`` percentages, ``成`` tenths, ``半`` halves and dates such as ``2026年10月11日`` become
Arabic numbers; CJK runs become character bigrams and trigrams, so passages match without word segmentation;
and negation and modality cues are read per clause.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from functools import lru_cache
from itertools import islice
from typing import NamedTuple

STOPWORDS = frozenset("""
a an the is are was were be been being am to of in on at by for with and or but as it its this that these
those from into than then there here we our you your they their i me my he she his her them us will would
shall should can could may might must do does did has have had so such also just very about over under
after before again via per if when while which who whom what where how all any each every some only own
same both more most other through not no never none nor always probably likely maybe perhaps seems think
""".split())
ZH_STOP = "的了是在和与及也都就而被把对为这那有我你他她它们个"
ZH_NEGATION = "不没未无非别勿"

_NEGATION_EN = (r"\b(?:not|no|never|none|nobody|nothing|neither|nor|without|cannot|no longer|\w+n't|"
                r"skip(?:s|ped|ping)?|bypass(?:es|ed|ing)?)\b")
NEGATION = re.compile(_NEGATION_EN + "|[" + ZH_NEGATION + "]", re.I)
NEGATION_EN = re.compile(_NEGATION_EN, re.I)
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
CJK = re.compile("[㐀-鿿]")
SUFFIXES = ("ations", "ation", "ments", "ment", "ions", "ion", "ency", "ence", "ings", "ing",
            "ies", "ied", "ed", "es", "al", "ly", "s", "e")

# Pairs of terms that cannot both describe the same state. English terms are stemmed on load.
# States only: add/remove, start/stop and open/close are things that happen in turn, and on real repositories they
# were noise every time.
ANTONYMS_EN = [("approve", "reject"), ("approve", "deny"), ("accept", "reject"), ("accept", "decline"),
               ("enable", "disable"), ("allow", "block"), ("allow", "forbid"), ("pass", "fail"),
               ("succeed", "fail"), ("true", "false"), ("include", "exclude"), ("increase", "decrease"),
               ("public", "private"), ("required", "optional"), ("supported", "deprecated")]
ANTONYMS_ZH = [("批准", "拒绝"), ("批准", "否决"), ("通过", "否决"), ("同意", "拒绝"), ("启用", "禁用"),
               ("开启", "关闭"), ("允许", "禁止"), ("成功", "失败"), ("增加", "减少"), ("支持", "弃用")]


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


ANTONYM_STEMS = [(stem(a), stem(b)) for a, b in ANTONYMS_EN]


# --- normalization -----------------------------------------------------------------------------------

_FULL_WIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)} | {0x3000: 0x20}


def fold_width(text: str) -> str:
    """Full-width ASCII forms and the ideographic space as ASCII (``１２０％`` -> ``120%``, ``，`` -> ``,``)."""
    return text.translate(_FULL_WIDTH)


ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
             "九": 9}
ZH_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10 ** 4, "亿": 10 ** 8}
_NUMERAL = "".join(ZH_DIGITS) + "".join(ZH_UNITS)
# A Chinese numeral is read as a quantity only before a measure word or unit ("三个月", "两名", "第二批");
# elsewhere 一, 三 and friends are usually part of a word (统一, 一律, 一起, 第三方, 十分).
ZH_MEASURE = ("(?:小时|钟头|分钟|毫秒|星期|工作日|百分点|公里|公斤|副本|"
              "[个位名人次家条项件台种天日周月年号秒倍元份张笔套轮期批版页层级岁遍趟章款组辆座米%])")
_QUARTER = re.compile(rf"(?<![\d每{_NUMERAL}])第?\s?([一二三四1-4])\s?季度")  # 第四季度, 四季度 -> Q4, as in English
# An ASCII comma between digits groups thousands; a full-width one only between full-width digits (１，２４０),
# since between ASCII digits it separates list items (20，30).
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))|(?<=[０-９])，(?=[０-９]{3}(?![０-９]))")
_PERCENT = re.compile(rf"百分之\s?(?:(\d+(?:\.\d+)?)|([{_NUMERAL}]+)(?:点([{''.join(ZH_DIGITS)}]+))?)")
_TENTHS = re.compile(rf"(?<![{_NUMERAL}])([一二三四五六七八九十])成(?![为功本员果熟立长绩交品型就效])")
_ZH_NUMBER = re.compile(rf"(?<![{_NUMERAL}])[{_NUMERAL}]+")
_AFTER_NUMBER = re.compile(rf"\s?{ZH_MEASURE}")
_MULTIPLIED = re.compile(r"(\d+(?:\.\d+)?)\s?(千万|百万|万|亿|千)(?![米克瓦])")
_HALF_AFTER = re.compile(rf"(\d+(?:\.\d+)?)({ZH_MEASURE})半")
_HALF = re.compile(rf"(?<![上下前后大多一过减对折])半(?!年度)(?=个?{ZH_MEASURE})")  # 半年度: semi-annual
_ZH_FULL_DATE = re.compile(r"(\d{4})\s?年\s?(\d{1,2})\s?月(?:\s?(\d{1,2})\s?[日号])?")
_CJK_WRAP = re.compile(r"(?<=[㐀-鿿])[ \t]*\r?\n[ \t]*(?:>[ \t]*)*(?=[㐀-鿿])")


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def zh_numeral(numeral: str, year: bool = False) -> int | None:
    """The value of a Chinese numeral (``一千二百万``, ``十五``, ``两千五``), or None if it is not one.

    Digits alone are read one by one only for a year (``二〇二六``); elsewhere ``三四`` means "three or four".
    """
    if all(c in ZH_DIGITS for c in numeral):
        if len(numeral) == 1:
            return ZH_DIGITS[numeral]
        return int("".join(str(ZH_DIGITS[c]) for c in numeral)) if year else None
    if numeral[0] not in ZH_DIGITS and numeral[0] != "十":
        return None
    total = section = 0
    digit = None
    for char in numeral:
        if char in ZH_DIGITS:
            if digit:
                return None  # two digits in a row (三四十): an approximation, not a number
            digit = ZH_DIGITS[char]
        elif ZH_UNITS[char] < 10 ** 4:
            section += (1 if digit is None else digit) * ZH_UNITS[char]
            digit = None
        else:
            section += digit or 0
            total = (total + section) * ZH_UNITS[char] if char == "亿" else total + section * ZH_UNITS[char]
            section, digit = 0, None
    if digit and numeral[-2] in ZH_UNITS:
        return total + section + digit * ZH_UNITS[numeral[-2]] // 10  # 两千五 = 2500, 一万五 = 15000
    return total + section + (digit or 0)


def _zh_number(match: re.Match) -> str:
    numeral, rest = match.group(), match.string[match.end():]
    value = zh_numeral(numeral, year=rest.lstrip().startswith("年"))
    if value is None:
        return numeral
    if _AFTER_NUMBER.match(rest) or (numeral[0] in ZH_DIGITS and any(c in "百千万亿" for c in numeral)):
        return str(value)
    return numeral


def _percent(match: re.Match) -> str:
    arabic, numeral, decimals = match.groups()
    if arabic:
        return arabic + "%"
    value = 100 if numeral == "百" else zh_numeral(numeral)  # 百分之百
    if value is None:
        return match.group()
    fraction = "." + "".join(str(ZH_DIGITS[c]) for c in decimals) if decimals else ""
    return f"{value}{fraction}%"


def _and_a_half(match: re.Match) -> str:
    number, measure = match.groups()
    if measure == "年" and len(number) == 4:
        return match.group()  # 2026年半年度 is a half-year report, not 2026.5 years
    return _number(Decimal(number) + Decimal("0.5")) + measure


def _date(match: re.Match) -> str:
    year, month, day = (int(g) if g else None for g in match.groups())
    try:
        date(year, month, day or 1)
    except ValueError:
        return match.group()
    # Spaced, so that "2026年3月2日：2027年" does not run together into one token "2026-03-02:2027".
    return f" {year:04d}-{month:02d}" + (f"-{day:02d} " if day else " ")


@lru_cache(maxsize=4096)  # a judgment reads the same claim and passage several times
def normalize(text: str) -> str:
    """The text the term extractors read.

    Full-width forms are folded to ASCII. In text with CJK ideographs, thousands separators are dropped and
    Chinese quantities are rewritten as Arabic numbers: quarters (``第四季度`` -> ``Q4``, as in English),
    numerals before a measure word (``三个月`` -> ``3个月``),
    multipliers (``1200万`` -> ``12000000``), percentages (``百分之十``, ``一成`` -> ``10%``), halves
    (``半小时`` -> ``0.5小时``, ``一年半`` -> ``1.5年``) and dates (``2026年10月11日`` -> ``2026-10-11``). A line break
    between two ideographs goes, with the indent or quote marker after it: Chinese takes no space, and a word can be split
    there, as in a claim's lines joined by ``memory._unwrap``.
    """
    if text.isascii():  # most of a repository, and nothing to fold
        return text
    if not CJK.search(text):
        return fold_width(text)
    text = fold_width(_THOUSANDS.sub("", _CJK_WRAP.sub("", text)))
    text = _QUARTER.sub(lambda m: f"Q{zh_numeral(m.group(1)) if m.group(1) in ZH_DIGITS else m.group(1)}", text)
    text = _PERCENT.sub(_percent, text)
    text = _TENTHS.sub(lambda m: f"{zh_numeral(m.group(1)) * 10}%", text)
    text = _ZH_NUMBER.sub(_zh_number, text)
    text = _MULTIPLIED.sub(lambda m: _number(Decimal(m.group(1)) * {"千万": 10 ** 7, "百万": 10 ** 6, "万": 10 ** 4,
                                                                    "亿": 10 ** 8, "千": 1000}[m.group(2)]), text)
    text = _HALF_AFTER.sub(_and_a_half, text)
    text = _HALF.sub("0.5", text)
    return _ZH_FULL_DATE.sub(_date, text)


# --- terms -------------------------------------------------------------------------------------------

_ZH_SPLIT = re.compile("[" + ZH_STOP + ZH_NEGATION + "]+")


def _cjk_pieces(run: str) -> list[str]:
    return [piece for piece in _ZH_SPLIT.split(run) if piece]


def _cjk_tokens(run: str) -> Iterator[str]:
    """A lone character, or the character bigrams and trigrams of each piece between stop characters."""
    for piece in _cjk_pieces(run):
        if len(piece) == 1:
            yield piece
        for i in range(len(piece) - 1):
            yield piece[i:i + 2]
            if i + 3 <= len(piece):
                yield piece[i:i + 3]


def _term(token: str) -> str | None:
    """The term for a Latin-script token, or None for a stopword."""
    if len(token) == 1 and token.isupper():
        return token  # identifiers such as "Feature A"; lowercase "a" is a stopword
    lower = token.lower().removesuffix("'s")
    if lower not in STOPWORDS and "'" not in lower:
        return stem(lower)
    return None


# Bounded: audit text is untrusted. Tokens repeat a lot in a repository, so over nine lookups in ten are hits, and
# passages that use a term share one string for it instead of keeping a copy each. A long token rarely repeats, and
# in a long-lived server the cache would hold on to it after the audit: ``_word`` is not asked about those.
_word = lru_cache(maxsize=1 << 16)(_term)
_LONGEST_CACHED = 64


def _tokens(text: str) -> Iterator[tuple[bool, str]]:
    """Yield (is_number, token) in text order: stemmed content words, CJK n-grams and numbers."""
    for token in TOKEN.findall(normalize(text)):
        if token[0].isdigit():
            yield True, token
        elif "㐀" <= token[0] <= "鿿":
            yield from ((False, piece) for piece in _cjk_tokens(token))
        elif (word := _word(token) if len(token) <= _LONGEST_CACHED else _term(token)) is not None:
            yield False, word


# Bounded: audit text is untrusted, so a memory cannot grow this without bound.
@lru_cache(maxsize=32768)
def _analyzed(text: str) -> tuple[tuple[bool, str], ...]:
    """Tokenize once. ``features`` and ``terms`` are asked for the same sentences over and over."""
    return tuple(_tokens(text))


@lru_cache(maxsize=4096)
def features(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (content tokens, numeric tokens)."""
    words, numbers = set(), set()
    for is_number, token in _analyzed(text):
        (numbers if is_number else words).add(token)
    return frozenset(words), frozenset(numbers)


def terms(text: str) -> list[str]:
    """The tokens behind ``features``, in text order, for comparing two versions of a sentence."""
    return [token for _, token in _analyzed(text)]


LATIN_WEIGHT = 2  # a Latin-script term weighs about as much as a Chinese word of two characters


def coverage_units(claim: str) -> tuple[list[int], dict[tuple[str, str], set[int]], dict[str, set[int]]]:
    """The units ``char_coverage`` counts for ``claim``, and what can find each of them.

    Returns (weight of each unit, key -> units it finds, claim-side opposing term -> units it finds).
    A key ``("word", term)`` finds its units when the passage has that term (a bigram, trigram or
    Latin-script word); ``("char", c)`` when the passage contains the lone character ``c`` anywhere.
    The audit's conflict index uses this to bound the coverage a passage can reach without a key.
    """
    weights: list[int] = []
    keys: dict[tuple[str, str], set[int]] = {}
    opposable: dict[str, set[int]] = {}
    english = {term for pair in ANTONYM_STEMS for term in pair}
    chinese = [term for pair in ANTONYMS_ZH for term in pair if term in claim]  # as opposing_terms reads it
    for match in TOKEN.finditer(normalize(claim)):
        token = match.group()
        if token[0].isdigit():
            continue
        if not CJK.match(token):
            if (word := _word(token) if len(token) <= _LONGEST_CACHED else _term(token)) is not None:
                keys.setdefault(("word", word), set()).add(len(weights))
                if word in english:
                    opposable.setdefault(word, set()).add(len(weights))
                weights.append(LATIN_WEIGHT)
            continue
        for piece in _cjk_pieces(token):
            base = len(weights)
            weights.extend([1] * len(piece))
            if len(piece) == 1:
                keys.setdefault(("char", piece), set()).add(base)
            for size in (2, 3):
                for i in range(len(piece) - size + 1):
                    keys.setdefault(("word", piece[i:i + size]), set()).update(range(base + i, base + i + size))
            for term in chinese:
                for at in (m.start() for m in re.finditer(re.escape(term), piece)):
                    opposable.setdefault(term, set()).update(range(base + at, base + at + len(term)))
    return weights, keys, opposable


def char_coverage(claim: str, passage: str) -> float:
    """Share of a CJK claim's content found in the passage, counted per character.

    Each CJK character of the claim is one unit, found when a bigram or trigram of the claim that contains
    it occurs in the passage (a lone character: when the passage contains it), so the junctions between
    words, which an unsegmented claim and passage rarely share, cost nothing. Each Latin-script content term
    (``API``, ``Q4``) is ``LATIN_WEIGHT`` units. The claim's side of an opposing pair counts as found.
    """
    passage_words, _ = features(passage)
    passage_chars = set(CJK.findall(normalize(passage)))
    opposed = opposing_terms(claim, passage)
    opposed = opposed[0] if opposed else None
    found = total = 0
    for match in TOKEN.finditer(normalize(claim)):
        token = match.group()
        if token[0].isdigit():
            continue
        if not CJK.match(token):
            if (word := _word(token) if len(token) <= _LONGEST_CACHED else _term(token)) is not None:
                total += LATIN_WEIGHT
                found += LATIN_WEIGHT * (word in passage_words or word == opposed)
            continue
        for piece in _cjk_pieces(token):
            hit = [len(piece) == 1 and piece in passage_chars] * len(piece)
            for size in (2, 3):
                for i in range(len(piece) - size + 1):
                    if piece[i:i + size] in passage_words:
                        hit[i:i + size] = [True] * size
            if opposed and CJK.match(opposed):
                for at in (m.start() for m in re.finditer(re.escape(opposed), piece)):
                    hit[at:at + len(opposed)] = [True] * len(opposed)
            total += len(piece)
            found += sum(hit)
    return found / total if total else 0.0


# --- negation and modality ---------------------------------------------------------------------------

ZH_NEGATION_CUES = ("不再", "不得", "禁止", "没有", "不", "没", "未", "无", "非", "别", "勿")
# Words with a negation character that negate nothing, and double negatives that oblige (非…不可, 不得不).
ZH_NOT_NEGATION = ("不仅", "不但", "不只", "不光", "不断", "不过", "不久", "不少", "不错", "不同", "不管", "不论",
                   "差不多", "不妨", "不时", "不然", "未来", "无论", "无线", "无数", "无比", "无疑", "无限", "无非",
                   "非常", "除非", "莫非", "是非", "没收", "淹没", "埋没", "出没", "沉没", "分别", "类别", "级别",
                   "区别", "特别", "识别", "告别", "个别", "差别", "性别", "别人", "别的", "别名", "辨别", "鉴别",
                   "派别", "别处", "有别", "不得不", "非得", "无不")
_CLAUSE = re.compile(r"[,;:。!?()\[\]\n]")
_IN_CLAUSE = r"[^,;:。!?()\[\]\n]{0,20}?"
_ZH_NOT_NEGATION = re.compile(f"非得?{_IN_CLAUSE}不可|" + "|".join(sorted(ZH_NOT_NEGATION, key=len, reverse=True)))
_ZH_NEGATION = re.compile("|".join(ZH_NEGATION_CUES))
# Two modality scales; one side at each end of a scale is a contradiction.
ZH_MODALITY = (
    ("scope", re.compile("所有|任何|一律|全部|凡是|每个|每一|一切|全体|无论|不论|不管"),
     re.compile("只有|只能|只对|只在|只允许|只限|仅|唯有")),
    ("obligation", re.compile(f"必须|务必|必需|(?<![无毋必])须(?!知)|非得|不得不|非{_IN_CLAUSE}不可"),
     re.compile("无需|无须|毋须|不必|不用|不需要?|不强制|可选|自愿|免于")),
)


def _clauses(text: str, other: str | None) -> list[str]:
    """Clauses of normalized CJK text; with ``other``, only those sharing a content term with it."""
    clauses = [c for c in _CLAUSE.split(normalize(text)) if c.strip()]
    if other is None:
        return clauses
    shared, _ = features(other)
    # A lone character (元, 年) is too weak a link between a clause and the other side.
    return [c for c in clauses if any(len(t) > 1 or not CJK.match(t) for t in features(c)[0] & shared)]


def negation(text: str, other: str | None = None) -> str | None:
    """The cue that negates ``text``, or None.

    Text without CJK is negated by any cue anywhere in it. In CJK text a cue counts only in a clause that
    shares a content term with ``other`` (when given), so a negation about something else in a long Chinese
    sentence does not count; words such as 未来, 非常 and 级别, and double negatives such as 非…不可, are not cues.
    """
    if not CJK.search(text):
        match = NEGATION.search(text)
        return match.group() if match else None
    for clause in _clauses(text, other):
        if match := NEGATION_EN.search(clause) or _ZH_NEGATION.search(_ZH_NOT_NEGATION.sub("_", clause)):
            return match.group()
    return None


@lru_cache(maxsize=32768)
def has_negation(text: str) -> bool:
    return negation(text) is not None


def modality_conflict(claim: str, passage: str) -> tuple[str, str] | None:
    """(claim cue, passage cue) when Chinese text puts the two sides at opposite ends of a modality scale:
    all (所有, 任何, 一律) against only (仅, 只有), or must (必须, 非…不可) against need not (无需, 不必).
    Only clauses that share a content term with the other side are read."""
    if not (CJK.search(claim) or CJK.search(passage)):
        return None
    sides = (_clauses(claim, passage), _clauses(passage, claim))
    for _, high, low in ZH_MODALITY:
        cues = []
        for clauses in sides:
            found = [(pattern, match.group()) for clause in clauses for pattern in (high, low)
                     if (match := pattern.search(clause))]
            ends = {pattern for pattern, _ in found}
            cues.append(found[0] if len(ends) == 1 else None)
        if cues[0] and cues[1] and cues[0][0] is not cues[1][0]:
            return cues[0][1], cues[1][1]
    return None


def opposing_terms(claim: str, passage: str) -> tuple[str, str] | None:
    """Return (claim term, passage term) if they name mutually exclusive states."""
    claim_words, _ = features(claim)
    passage_words, _ = features(passage)
    for a, b in ANTONYM_STEMS:
        for x, y in ((a, b), (b, a)):
            if x in claim_words and y in passage_words and x not in passage_words:
                return x, y
    if CJK.search(claim) and CJK.search(passage):
        claim, passage = normalize(claim), normalize(passage)  # 禁用 can be wrapped over two lines
        for a, b in ANTONYMS_ZH:
            for x, y in ((a, b), (b, a)):
                if x in claim and y in passage and x not in passage:
                    return x, y
    return None


def explicit_dates(text: str) -> list[date]:
    found = []
    text = fold_width(text)
    for pattern in (ISO_DATE, ZH_DATE):
        for y, m, d in pattern.findall(text):
            try:
                found.append(date(int(y), int(m), int(d)))
            except ValueError:
                pass
    return found


_LINE = re.compile(r"[^\n]+")
_LEAD = re.compile(r"^(?:\s*(?:#{1,6}|>|[-*+]|\d+[.)]|\[[ xX]\])\s+)+")
_BOUNDARY = re.compile(r"[。！？；]|[.!?;](?=\s|$)")
_WORD_CHARACTER = re.compile(r"\w")
# The dot of an abbreviation that is always followed by more of the sentence, however the next word is written.
_GOES_ON = re.compile(r"(?<![^\W_])(?:e\.g|i\.e|cf|vs|viz|a\.k\.a|w\.r\.t|approx|incl|excl|resp)\.$", re.I)
# The same, only when a number follows ("no. 5") or a word in lower case ("etc. and more"): either can also end one.
_BEFORE_NUMBER = re.compile(r"(?<![^\W_])(?:no|nos|fig|figs|sec|ver|vol|pp?|ch|eq)\.$", re.I)
_ETC = re.compile(r"(?<![^\W_])etc\.$", re.I)
_VERSION_PART = re.compile(r"(?<![\w.])v?\d+(?:\.\d+)*\.$")  # "3. 13": a version with a stray space


def _ends_sentence(body: str, end: int) -> bool:
    """Whether the boundary ending at ``end`` closes a sentence: a dot may belong to an abbreviation."""
    if body[end - 1] != ".":
        return True
    head = body[max(0, end - 24):end]
    if _GOES_ON.search(head):
        return False
    rest = body[end:end + 16].lstrip()
    if head.endswith("...") and rest[:1].islower():  # an ellipsis in the middle of a sentence
        return False
    if _ETC.search(head):
        return not (rest[:1].islower() or rest[:1] in (")", ",", ";"))
    return not (rest[:1].isdigit() and (_BEFORE_NUMBER.search(head) or _VERSION_PART.search(head)))


def whole_span(text: str) -> tuple[int, int] | None:
    """[start, end) of ``text`` with its markdown markers trimmed, if anything is left: a table row is read whole."""
    lead = _LEAD.match(text)
    offset = lead.end() if lead else len(text) - len(text.lstrip())
    stripped = text[offset:].rstrip()
    return (offset, offset + len(stripped)) if _WORD_CHARACTER.search(stripped) else None


def sentence_spans(text: str) -> Iterator[tuple[int, int]]:
    """Yield [start, end) character spans of sentences, with markdown list/heading markers trimmed. An abbreviation's
    dot (e.g., i.e., vs., etc. before more of the sentence) does not end one."""
    for line in _LINE.finditer(text):
        base, body, pos = line.start(), line.group(), 0
        cuts = [m.end() for m in _BOUNDARY.finditer(body) if _ends_sentence(body, m.end())] + [len(body)]
        for cut in cuts:
            segment = body[pos:cut]
            lead = _LEAD.match(segment)
            offset = lead.end() if lead else len(segment) - len(segment.lstrip())
            stripped = segment[offset:].rstrip()
            if _WORD_CHARACTER.search(stripped):
                yield base + pos + offset, base + pos + offset + len(stripped)
            pos = cut


# --- clauses -----------------------------------------------------------------------------------------

# Where one statement ends and the next begins. Not inside 14:00 or localhost:5420.
_STATEMENT_BREAK = re.compile(r":(?![\d/])|;|\||[。！？]|\s[-–—]\s")
# Inside a statement. Not inside 1,000.
_CLAUSE_BREAK = re.compile(rf"{_STATEMENT_BREAK.pattern}|,(?!\d)|、|\s(?:and|but)\s")
# Every clause is compared with every clause and text in an audit is untrusted, so both are bounded.
_SPLIT_UP_TO = 2000  # characters: a longer text is one part
_MOST_PARTS = 32  # parts read from a text, and numbers


def _spans(text: str, breaks: re.Pattern) -> Iterator[tuple[int, int]]:
    """[start, end) of the parts of ``text`` between ``breaks``; a very long text is one part."""
    at = 0
    if len(text) <= _SPLIT_UP_TO:
        for found in breaks.finditer(text):
            yield at, found.start()
            at = found.end()
    yield at, len(text)


def _split(text: str, breaks: re.Pattern) -> list[str]:
    text = normalize(text)
    return [part for start, end in _spans(text, breaks) if (part := text[start:end].strip())][:_MOST_PARTS]


def statements(text: str) -> list[str]:
    """The parts of normalized ``text`` between colons, semicolons, table bars and spaced dashes."""
    return _split(text, _STATEMENT_BREAK)


def clauses(text: str) -> list[str]:
    """The statements of ``text``, split further at commas and at ``and`` and ``but``."""
    return _split(text, _CLAUSE_BREAK)


def _content_terms(text: str) -> list[str]:
    """Stemmed content words, and lone CJK characters, in text order; numbers are left out."""
    found: list[str] = []
    for match in TOKEN.finditer(text):
        token = match.group()
        if token[0].isdigit():
            continue
        if CJK.match(token):
            found += [char for piece in _cjk_pieces(token) for char in piece]
        elif (word := _word(token) if len(token) <= _LONGEST_CACHED else _term(token)) is not None:
            found.append(word)
    return found


# --- named values ------------------------------------------------------------------------------------

_NO_VALUE = frozenset({"none", "null", "nil", "undefined"})
_BOOLEAN = frozenset({"true", "false"})
_NUMBER = re.compile(r"([-+]?\d[\d_]*(?:,\d{3})*(?:\.\d+)?)([A-Za-z%]{0,3})")
_NAME = r"[A-Za-z_][\w.-]*"
# A literal: quoted without spaces, a number with at most a short unit, a boolean, or a null (which means no value).
_LITERAL = (r"(?P<value>\"[^\"\s{}$]{1,40}\"|'[^'\s{}$]{1,40}'|`[^`\s{}$]{1,40}`|[-+]?\d[\d_,.]{0,40}[A-Za-z%]{0,3}(?![\w-])|"
            r"(?:[Tt]rue|[Ff]alse|TRUE|FALSE|[Nn]one|null|nil|undefined)(?![\w-]))")
_NUMERIC = r"(?P<value>[-+]?\d[\d_,.]{0,40}[A-Za-z%]{0,3}(?![\w-]))"
_KEYED = rf"(?<![\w./@#-])(?P<quote>[`\"']?)(?P<key>{_NAME})(?P=quote)"
DEFAULT, STATED, ENTRY, SET = "default", "stated", "entry", "set"
_BINDINGS = tuple((kind, re.compile(pattern)) for kind, pattern in (
    # key (default: 100), `key` (optional, default `100`)
    (DEFAULT, _KEYED + r"\s*[(\[]\s*(?:[^()\[\]\n]{0,40}?[,;]\s*)?defaults?(?:\s+(?:is|to|of))?\s*[:=]?\s*" + _LITERAL),
    # key: int = 180, key: float = Field(default=0.1, ...). The type starts on the first character that is not a space
    # and takes in any more of them: no two ways to read a run of spaces, since a claim's or a line's can be very long.
    (DEFAULT, _KEYED + r"\s*:\s*(?=\S)[\w.\[\], |\"'\s]{1,60}?=\s*(?:\w*[Ff]ield\(\s*(?:\.\.\.\s*,\s*)?(?:default\s*=\s*)?)?"
     + _LITERAL),
    # key defaults to 100
    (DEFAULT, _KEYED + r"\s+defaults?\s+(?:to|is)\s+" + _LITERAL),
    # key: 100, "key": 5
    (ENTRY, _KEYED + r"\s*:(?!=)\s*" + _LITERAL),
    # | key | 100 |, | key | int | 100 |
    (STATED, r"\|\s*" + _KEYED + r"\s*\|\s*(?:`?(?:int|float|str|string|bool|boolean|number)`?\s*\|\s*)?" + _LITERAL
     + r"\s*(?=\||$)"),
    # `key` is 100: only a number, since ``x is True`` is mostly a condition in code
    (STATED, _KEYED + r"\s+is\s+" + _NUMERIC),
    # key=100, key = 100, key := 100
    (SET, _KEYED + r"\s*:?=(?!=)\s*" + _LITERAL),
    # --flag 100
    (SET, r"(?<![\w-])--(?P<key>[A-Za-z][\w-]*)(?:=|\s+)" + _LITERAL),
))


class Binding(NamedTuple):
    kind: str  # DEFAULT, STATED, ENTRY (``key: 100``, in code one object's field), or SET (one place, not a default)
    value: str  # compares by meaning
    written: str


_MOST_BINDINGS = 8  # different ones of one key read from a text: named_conflict compares each with each


def _is_key(name: str, quote: str) -> bool:
    """Whether a name reads as a setting: snake_case, kebab-case, camelCase, dotted, or written in backticks."""
    if quote == "`" or "_" in name or "-" in name or re.search(r"[a-z][A-Z]", name):
        return True
    return "." in name and all(len(part) > 1 for part in name.split("."))


def _literal(raw: str) -> str | None:
    """A literal in a form that compares by meaning; None for a null or nothing usable."""
    value = raw.strip("`\"' ").rstrip(".,")
    low = value.lower()
    if not value or low in _NO_VALUE:
        return None
    if low in _BOOLEAN:
        return low
    if number := _NUMBER.fullmatch(value):
        amount = Decimal(number.group(1).replace("_", "").replace(",", ""))
        return _number(abs(amount) if amount.is_zero() else amount) + number.group(2).lower()
    return low


def _binds(match: re.Match) -> bool:
    """Whether a pattern's match names a setting, rather than a word that happens to stand before a value."""
    groups = match.groupdict()
    return "quote" not in groups or _is_key(groups["key"].rstrip(".-"), groups["quote"])


@lru_cache(maxsize=32768)
def named_values(text: str) -> dict[str, tuple[Binding, ...]]:
    """key (lowercase) -> what ``text`` binds it to. Numbers, booleans and short quoted strings; a null is no value."""
    text = fold_width(text)
    found: dict[str, list[Binding]] = {}
    for kind, pattern in _BINDINGS:
        for match in pattern.finditer(text):
            if _binds(match) and (value := _literal(match.group("value"))) is not None:
                written = match.group("value").strip("`\"' ").rstrip(".,")
                key = match.group("key").rstrip(".-")
                # MAX_STEPS = 100 declares a constant; it is not one assignment among others.
                binding = Binding(STATED if kind == SET and key.isupper() else kind, value, written)
                bindings = found.setdefault(key.lower(), [])
                if len(bindings) < _MOST_BINDINGS and binding not in bindings:
                    bindings.append(binding)
    return {key: tuple(bindings) for key, bindings in found.items()}


_SHORT_UNITS = {"m": "min", "d": "day"}  # a bound value's ``30m`` and ``7d``; beside a number in prose, ``m`` is often metres


def _unit(written: str) -> str | None:
    return _UNITS.get(written) or _SHORT_UNITS.get(written)


def _differs(one: str, other: str) -> bool:
    """Whether two comparable values are different, as far as text can tell."""
    first, second = _NUMBER.fullmatch(one), _NUMBER.fullmatch(other)
    if first and second and first.group(2) and second.group(2) and first.group(2) != second.group(2):
        # 500ms and 1s: units the text does not convert. The same digits in another unit of that kind are another amount.
        units = _unit(first.group(2)), _unit(second.group(2))
        return (first.group(1) == second.group(1) and None not in units
                and _UNIT_KINDS[units[0]] == _UNIT_KINDS[units[1]] and units[0] != units[1])
    if first and second:
        return first.group(1) != second.group(1)
    return one != other and not one.startswith(other + ".") and not other.startswith(one + ".")


class ValueConflict(NamedTuple):
    key: str
    claim: str
    source: str
    merely_set: bool  # a side only sets it somewhere, which says less than a statement of what it is


def named_conflict(claim: str, passage: str, set_counts: bool = True, declared: bool = False) -> ValueConflict | None:
    """What differs when both texts bind the same key to different values.

    A value set somewhere (``key=100``, ``key = 100``, ``--flag 100``) is not compared with a stated default (``key
    (default: 3)``, ``key: int = 3``): setting 100 in one place says nothing about the default. Without ``set_counts``,
    a value set somewhere is not compared at all. With ``declared``, for a passage in source code, nor is one of its
    ``key: 100``: there that is a field of one object or call, as ``key=100`` is one assignment."""
    theirs = named_values(passage)
    for key, ours in named_values(claim).items():
        pairs = [(a, b) for a in ours for b in theirs.get(key, ())
                 if {a.kind, b.kind} != {DEFAULT, SET} and (set_counts or SET not in (a.kind, b.kind))
                 and not (declared and b.kind == ENTRY)]
        if pairs and all(_differs(a.value, b.value) for a, b in pairs):
            return ValueConflict(key, pairs[0][0].written, pairs[0][1].written,
                                 all(SET in (a.kind, b.kind) for a, b in pairs))
    return None


# --- numbers -----------------------------------------------------------------------------------------

# A number or date standing on its own: not part of a name, path, issue reference, time or port, and not the operand
# of an operator (``lines.length + 1``). It may group its thousands (``1,000``) or be a release with a wildcard
# (``18.x``), and a rate is a number too (``100/min``).
_QUANTITY = re.compile(r"(?<![A-Za-z0-9_.\-/@#:+%])(?<![-+*/] )(?<!== )(?<!!= )v?"
                       r"(\d{4}-\d{2}-\d{2}|(?:\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.\d+)*(?:\.[xX](?![A-Za-z0-9_]))?)"
                       r"(?:%|[A-Za-z]{1,3}(?![A-Za-z0-9_]))?"
                       r"(?:/(?:s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?|d|days?|weeks?|mo|months?|y|yrs?|years?)(?![A-Za-z0-9_]))?"
                       r"(?![A-Za-z0-9_/:]|[.\-][A-Za-z0-9_])")
_TERMINAL_ESCAPE = re.compile(r"(?:\x1b|\\u001b|\\x1b)\[[0-9;]*[A-Za-z]")  # raw, or as a report stores it
_NEARBY = 80  # characters read on each side of a number to find the words next to it
_AFTER = 200  # characters read after a number to find what its clause goes on to say
# Units that measure the same thing; a sentence that keeps the digits and changes the unit changes the amount.
_UNITS = {
    **dict.fromkeys(("ms", "msec", "millisecond", "milliseconds", "milisegundo", "milisegundos", "毫秒"), "ms"),
    **dict.fromkeys(("s", "sec", "secs", "second", "seconds", "segundo", "segundos", "秒"), "s"),
    **dict.fromkeys(("min", "mins", "minute", "minutes", "minuto", "minutos", "分钟"), "min"),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours", "hora", "horas", "小时"), "h"),
    **dict.fromkeys(("day", "days", "día", "días", "dia", "dias", "天"), "day"),
    **dict.fromkeys(("week", "weeks", "semana", "semanas", "周", "星期"), "week"),
    **dict.fromkeys(("month", "months", "mes", "meses", "个月"), "month"),
    **dict.fromkeys(("year", "years", "año", "años", "年"), "year"),
    **{size: size for size in ("kb", "mb", "gb", "tb", "pb", "kib", "mib", "gib", "tib", "pib")},
}
_UNIT_KINDS = {**dict.fromkeys(("ms", "s", "min", "h", "day", "week", "month", "year"), "time"),
               **dict.fromkeys(("kb", "mb", "gb", "tb", "pb", "kib", "mib", "gib", "tib", "pib"), "size")}
_UNIT_AFTER = re.compile(r"\s?(毫秒|分钟|小时|个月|星期|秒|天|周|年|[^\W\d_㐀-鿿]+)")


class Quantity(NamedTuple):
    value: str
    unit: str | None  # a unit of time or size right after the number
    written: str  # the number and its unit, as written
    next_to: frozenset[str]  # the content terms right next to it
    clause: str
    after: frozenset[str]  # the content terms after it in its clause


def is_date(value: str) -> bool:
    return "-" in value


def _quantity(digits: str) -> str:
    """The number without zero padding or grouping: 0723 and 723, 2.0 and 2, 1,000 and 1000 are one value; 3.10 and 3.1
    stay two. A wildcard stays: 18.x is written with a lower-case x."""
    if is_date(digits):
        return digits
    head, dot, rest = digits.replace(",", "").lower().partition(".")
    head = head.lstrip("0") or "0"
    return head if not dot or set(rest) == {"0"} else f"{head}.{rest}"


def same_release(one: str, other: str) -> bool:
    """Whether a value with a wildcard takes in the other: 3.x and 3.12, 3.x and 3, but not 3.x and 2.x."""
    return any(wild.endswith(".x") and (exact == wild[:-2] or exact.startswith(wild[:-1]))
               for wild, exact in ((one, other), (other, one)))


def _unbound(text: str) -> str:
    """``text`` with the values bound to a key blanked: those are for ``named_conflict``, not for number comparison."""
    for _, pattern in _BINDINGS:
        text = pattern.sub(lambda match: " " * len(match.group()) if _binds(match) or match.groupdict().get("quote")
                           else match.group(), text)
    return text


@lru_cache(maxsize=4096)
def quantities(text: str) -> tuple[Quantity, ...]:
    """Each number in ``text`` that stands on its own and is not bound to a key."""
    text = _unbound(_TERMINAL_ESCAPE.sub(" ", normalize(text)))
    found = []
    for at, end in _spans(text, _CLAUSE_BREAK):
        part = text[at:end]
        for match in _QUANTITY.finditer(part):
            start = at + match.start()
            rest = text[at + match.end(1):at + match.end(1) + _NEARBY]
            before = _content_terms(text[max(0, start - _NEARBY):start])
            word = _UNIT_AFTER.match(rest)
            unit = _UNITS.get(word.group(1).lower()) if word else None
            written = match.group(1) + word.group() if unit else match.group().strip()
            past = match.end(1) + word.end() if unit else match.end()  # its unit is what it measures, not a case
            found.append(Quantity(_quantity(match.group(1)), unit, written,
                                  frozenset(before[-1:] + _content_terms(rest)[:2]), part.strip(),
                                  frozenset(_content_terms(part[past:past + _AFTER]))))
            if len(found) == _MOST_PARTS:
                return tuple(found)
    return tuple(found)


def units_differ(one: Quantity, other: Quantity) -> bool:
    """The same number with a different unit of the same kind: 2 minutes and 2 seconds, 512 GB and 512 GiB."""
    return (one.value == other.value and one.unit is not None and other.unit is not None and one.unit != other.unit
            and _UNIT_KINDS[one.unit] == _UNIT_KINDS[other.unit])


# --- scoped negation ---------------------------------------------------------------------------------

NEGATION_SCOPE = 3  # content words after a cue that it can reach
_CUE_WINDOW = 200  # characters after a cue read to find them
_MOST_CUES = 32  # cues read in one clause: more of them say nothing more, and a passage can hold thousands
# As NEGATION_EN, less what is mostly code (``None``, ``not(...)``, ``is not None``) or names (``--no-cache``), plus
# the contrasts that put a word aside just as well.
_CUE = re.compile(r"(?<![-_./`])\b(?:not(?!\(|\s+(?:none|null|nil|undefined)\b)|no longer|no|never|nobody|nothing|neither|"
                  r"nor|without|cannot|none of|instead of|rather than|\w+n't|skip(?:s|ped|ping)?|"
                  r"bypass(?:es|ed|ing)?)\b(?![-_])", re.I)
_ABSOLUTE_WORD = re.compile(r"\b(?:always|every|everyone|everything|never)\b", re.I)
_PROHIBITION = re.compile(r"^\W*(?:please\s+)?(?:do not|don't|never|skip)\b", re.I)


# snake_case, kebab-case, dotted and camelCase names, --flags and call() names, each read as one term here.
_WHOLE_NAME = re.compile(r"(?<![\w.-])(?:-{0,2}[A-Za-z_]\w*(?:[._-]+\w+)+|--\w+|[a-z]+[A-Z]\w*|[A-Za-z_]\w*\(\))")


def plain_words(text: str) -> str:
    """``text`` without its names and code spans, whose words (``allowed_domains``, ``.enable(``, `` `Closes #1` ``) are
    no statement of anything."""
    return _WHOLE_NAME.sub(" ", re.sub(r"`[^`\n]*`", " ", text))


def _whole_terms(text: str) -> list[str]:
    """Content terms in text order, with a name as one lowercase term."""
    found: list[str] = []
    at = 0
    for match in _WHOLE_NAME.finditer(text):
        found += _content_terms(text[at:match.start()])
        found.append(match.group().lower())
        at = match.end()
    return found + _content_terms(text[at:])


def stated_terms(text: str) -> frozenset[str]:
    return frozenset(_whole_terms(normalize(text)))


@lru_cache(maxsize=4096)
def reached_terms(text: str) -> frozenset[str]:
    """The terms a negation cue reaches in Latin-script ``text``, the next few of the cue's clause, unless the text
    also states them plainly: before a cue, or in a clause without one."""
    reached: set[str] = set()
    plain: set[str] = set()
    for part in clauses(text):
        cues = list(islice(_CUE.finditer(part), _MOST_CUES))
        plain.update(_whole_terms(part[:cues[0].start()] if cues else part))
        for match in cues:
            reached.update(_whole_terms(part[match.end():match.end() + _CUE_WINDOW])[:NEGATION_SCOPE])
    return frozenset(reached - plain)


def has_cue(text: str) -> bool:
    return bool(_CUE.search(text))


def is_absolute(text: str) -> bool:
    """A statement with ``always``, ``every`` or ``never`` in it. Opening with one, it is an instruction."""
    match = _ABSOLUTE_WORD.search(text)
    return bool(match) and bool(re.search(r"\w", text[:match.start()]))


def is_prohibition(text: str) -> bool:
    """A line opening with ``Do not``, ``Never`` or ``Skip``: an instruction, not a statement of how things are."""
    return bool(_PROHIBITION.match(text))


_REMOVED = re.compile(r"\b(?:is|are|was|were|been)\s+(?:\w+\s+)?(?:removed|deleted|dropped|retired)\b", re.I)
_GONE = re.compile(r"\b(?:was|were|been)\s", re.I)  # ``is removed`` may also say what happens each time
_REMOVAL_WORDS = frozenset(stem(word) for word in ("remove", "delete", "drop", "dropped", "retire"))


def removal(text: str) -> tuple[frozenset[str], bool]:
    """The content terms before a ``was removed`` (``is deleted``, ``were dropped``...) in ``text``, and whether that says
    it is gone for good, in the past tense."""
    match = _REMOVED.search(text)
    return (features(text[:match.start()])[0], bool(_GONE.match(match.group()))) if match else (frozenset(), False)


def says_removal(terms: frozenset[str]) -> bool:
    return bool(terms & _REMOVAL_WORDS)
