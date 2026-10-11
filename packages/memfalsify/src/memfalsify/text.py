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
    (``半小时`` -> ``0.5小时``, ``一年半`` -> ``1.5年``) and dates (``2026年10月11日`` -> ``2026-10-11``).
    """
    if not CJK.search(text):
        return fold_width(text)
    text = fold_width(_THOUSANDS.sub("", text))
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


def _word(token: str) -> str | None:
    """The term for a Latin-script token, or None for a stopword."""
    if len(token) == 1 and token.isupper():
        return token  # identifiers such as "Feature A"; lowercase "a" is a stopword
    lower = token.lower().removesuffix("'s")
    if lower not in STOPWORDS and "'" not in lower:
        return stem(lower)
    return None


def _tokens(text: str) -> Iterator[tuple[bool, str]]:
    """Yield (is_number, token) in text order: stemmed content words, CJK n-grams and numbers."""
    for match in TOKEN.finditer(normalize(text)):
        token = match.group()
        if token[0].isdigit():
            yield True, token
        elif "㐀" <= token[0] <= "鿿":
            yield from ((False, piece) for piece in _cjk_tokens(token))
        elif (word := _word(token)) is not None:
            yield False, word


@lru_cache(maxsize=4096)
def features(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (content tokens, numeric tokens)."""
    words, numbers = set(), set()
    for is_number, token in _tokens(text):
        (numbers if is_number else words).add(token)
    return frozenset(words), frozenset(numbers)


def terms(text: str) -> list[str]:
    """The tokens behind ``features``, in text order, for comparing two versions of a sentence."""
    return [token for _, token in _tokens(text)]


LATIN_WEIGHT = 2  # a Latin-script term weighs about as much as a Chinese word of two characters


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
            if (word := _word(token)) is not None:
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
