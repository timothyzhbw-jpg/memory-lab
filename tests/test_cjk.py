"""Lexical judging of Chinese text: normalization, n-gram terms, coverage, negation and modality cues."""
from pathlib import Path

import pytest

from conftest import EXAMPLES
from memfalsify.memory import parse_memory
from memfalsify.text import (CJK, explicit_dates, features, fold_width, has_negation, modality_conflict, negation,
                             normalize, number_sets_conflict, terms, zh_numeral)
from memfalsify.verify import LexicalVerifier, coverage

judge = LexicalVerifier().judge
BENCHMARK = Path(__file__).resolve().parent.parent / "benchmarks" / "memfalsify" / "cases"


def numbers(text):
    return set(features(text)[1])


# --- English stays as it was ---------------------------------------------------------------------------

def _english_lines():
    roots = [EXAMPLES / "memfalsify", *sorted(p for p in BENCHMARK.iterdir() if not p.name.startswith("zh-"))]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".md", ".txt", ".py", ".yaml", ".toml", ".cfg", ".json", ""}:
                yield from path.read_text(encoding="utf-8").splitlines()


def test_english_text_is_read_unchanged():
    lines = list(_english_lines())
    assert len(lines) > 300 and not any(CJK.search(line) for line in lines)
    assert all(normalize(line) == line for line in lines)


def test_english_negation_is_unscoped():
    # Without CJK, any cue anywhere negates, exactly as before; ``other`` is ignored.
    assert negation("The cache is warm, but sessions are not stored.", "The cache is warm.") == "not"
    assert modality_conflict("All endpoints retry.", "Only idempotent endpoints retry.") is None


# --- full-width forms -----------------------------------------------------------------------------------

def test_full_width_forms_fold_to_ascii():
    assert fold_width("ＡＰＩ　限流：１２０％（ｑ４）") == "API 限流:120%(q4)"
    assert features("ＡＰＩ 限流是每分钟 １００ 次。") == features("API 限流是每分钟 100 次。")
    assert numbers("团队数为１，２４０个") == {"1240"}
    assert explicit_dates("２０２６－１０－１１：发布") == explicit_dates("2026-10-11: release")


def test_thousands_separators():
    assert numbers("首月周活跃团队数为1,240个") == {"1240"}
    assert numbers("分别是 20，300 个") == {"20", "300"}  # a full-width comma between ASCII digits lists items
    assert numbers("1,240 teams") == {"1", "240"}  # English is unchanged


# --- Chinese numerals, units and dates --------------------------------------------------------------------

@pytest.mark.parametrize("numeral, value", [
    ("三", 3), ("两", 2), ("十", 10), ("十五", 15), ("二十", 20), ("一百零五", 105), ("两千五", 2500),
    ("一万五", 15000), ("一千二百万", 12_000_000), ("一亿二千万", 120_000_000), ("三四", None), ("三四十", None),
    ("万一", None), ("千万", None),
])
def test_zh_numeral(numeral, value):
    assert zh_numeral(numeral) == value


def test_digit_by_digit_numerals_only_for_years():
    assert zh_numeral("二〇二六", year=True) == 2026
    assert numbers("二〇二六年十月十一日上线") == {"2026-10-11"}


@pytest.mark.parametrize("text, expected", [
    ("应用日志保存三个月", {"3"}),
    ("至少需要两名评审人", {"2"}),
    ("每个合并请求至少需要一名评审人", {"1"}),
    ("超过五万的采购", {"50000"}),
    ("2026年研发预算为1200万元", {"2026", "12000000"}),
    ("超过 5 万元的采购", {"50000"}),
    ("融资1.5亿元", {"150000000"}),
    ("预算3千元", {"3000"}),
    ("百分之十的用户", {"10"}),
    ("百分之三点五的用户", {"3.5"}),
    ("百分之 30 的用户", {"30"}),
    ("百分之百的用户", {"100"}),
    ("一成的网页端用户", {"10"}),
    ("两成的用户", {"20"}),
    ("九成五的用户", {"95"}),
    ("一成五的用户", {"15"}),
    ("十成的用户", {"100"}),
    ("观察半小时", {"0.5"}),
    ("观察半年", {"0.5"}),
    ("半个月后", {"0.5"}),
    ("一年半", {"1.5"}),
    ("两个半小时", {"2.5"}),
    ("2026年10月11日上线", {"2026-10-11"}),
    ("2027年1月版本", {"2027-01"}),
    ("2025年12月18日：2026年预算", {"2025-12-18", "2026"}),
])
def test_chinese_quantities_become_numbers(text, expected):
    assert numbers(text) == expected


@pytest.mark.parametrize("text", [
    "新服务一律接入统一 API 网关", "第三方插件市场", "这一点十分重要", "三四个人", "万一失败", "千万不要上线",
    "下半年研发预算", "一半的用户", "进一步优化", "占三分之一", "一起上线", "2026年半年度报告",
    "半年报", "半年刊", "九成功",
])
def test_words_with_numeral_characters_stay_words(text):
    assert numbers(text) <= {"2026"}


def test_quarters_read_like_english():
    assert features("数据库切换在第四季度")[0] >= {"q4"} and numbers("今年四季度") == set()
    assert "q4" in features("在四季度")[0] and "q1" in features("第1季度")[0]
    assert "q1" in features("第一季度")[0] and "q1" in features("到一季度")[0]
    assert judge("数据库切换在第四季度。", "数据库切换在 Q4。").relation == "supports"
    # Q4 against Q1 is a different term, not a different number; nor does it support the claim.
    assert judge("数据库切换在第四季度。", "数据库切换推迟到第一季度。").relation == "unrelated"


@pytest.mark.parametrize("text, token", [
    ("下一季度", "nextq"), ("上一季度", "prevq"), ("这一季度", "thisq"), ("前一季度", "priorq"),
    ("后一季度", "laterq"), ("最后一季度", "lastq"),
])
def test_relative_quarters_are_not_the_first_quarter(text, token):
    words = features(text)[0]
    assert "q1" not in words and token in words
    judgment = judge(f"数据库切换在{text}。", "数据库切换在第一季度。")
    assert judgment.relation == "unrelated"


def test_every_quarter_stays_a_quantifier():
    # 每 already blocked Q1. Rewriting it away would hide the modality cue 每一.
    assert "q1" not in features("每一季度")[0] and "eachq" not in features("每一季度")[0]
    assert modality_conflict("每一季度都要评审。", "仅第一季度要评审。") == ("每一", "仅")


@pytest.mark.parametrize("claim, passage, relation", [
    ("预算是1200万元。", "预算为一千二百万元。", "supports"),
    ("预算是1200万元。", "预算为1500万元。", "contradicts"),
    ("新版首页向一成的用户开放。", "新版首页向10%的用户开放。", "supports"),
    ("新版首页向百分之十的用户开放。", "新版首页向２０％的用户开放。", "contradicts"),
    ("协作空间在2026年10月11日发布。", "协作空间已于 2026-10-11 发布。", "supports"),
    ("协作空间在2026年10月11日发布。", "协作空间已于 2026-10-12 发布。", "contradicts"),
    ("应用日志保存三个月。", "应用日志保存 6 个月。", "contradicts"),
    ("灰度观察半小时。", "灰度观察 0.5 小时。", "supports"),
    ("新版首页向九成五的用户开放。", "新版首页向95%的用户开放。", "supports"),
    ("新版首页向九成五的用户开放。", "新版首页向90%的用户开放。", "contradicts"),
    ("协作空间在2026年10月发布。", "协作空间已于 2026-10-11 发布。", "supports"),
    ("协作空间在2026年10月发布。", "协作空间已于 2026-11-02 发布。", "contradicts"),
    ("协作空间在2026年发布。", "协作空间已于2026年10月11日发布。", "supports"),
    ("协作空间在2026年发布。", "协作空间已于2027年1月发布。", "contradicts"),
    ("2026年10月预算为1200万元。", "2026-10-11预算为1500万元。", "contradicts"),
    ("2026年10月有5个版本。", "2026-10-11发布了版本。", "contradicts"),
])
def test_numeric_conflicts_across_notations(claim, passage, relation):
    judgment = judge(claim, passage)
    assert judgment.relation == relation
    if relation == "contradicts":
        assert judgment.rationale.startswith("different values")


def test_calendar_prefix_does_not_use_a_raw_string_prefix():
    assert number_sets_conflict(frozenset({"2026-1"}), frozenset({"2026-10"}))
    assert number_sets_conflict(frozenset({"10"}), frozenset({"100"}))
    assert number_sets_conflict(frozenset({"2026-10-11"}), frozenset({"2026-10-12"}))
    assert number_sets_conflict(frozenset({"2026-10", "12000000"}), frozenset({"2026-10-11", "15000000"}))
    assert number_sets_conflict(frozenset({"2026-10", "5"}), frozenset({"2026-10-11"}))
    assert not number_sets_conflict(frozenset({"2026-10"}), frozenset({"2026-10-11"}))
    # One shared exact value still suppresses the check.
    assert not number_sets_conflict(frozenset({"2026-10-11", "5"}), frozenset({"2026-10-11"}))
    assert judge("The release is in 2026.", "The release was on 2026-10-11.").relation == "supports"
    mismatch = judge("Shipped on 2026-02-10.", "Shipped on 2026-06-24.")
    assert mismatch.relation == "contradicts" and mismatch.rationale.startswith("different values")


# --- terms and coverage -----------------------------------------------------------------------------------

def test_cjk_bigrams_and_trigrams():
    words, _ = features("数据库迁移")
    assert words == {"数据", "数据库", "据库", "据库迁", "库迁", "库迁移", "迁移"}
    assert terms("主库迁移") == ["主库", "主库迁", "库迁", "库迁移", "迁移"]
    assert features("的")[0] == frozenset() and features("秒")[0] == {"秒"}  # stop characters split runs


def test_coverage_counts_characters_not_junctions():
    # "库切" spans two words; the passage says the same thing with 的 between them.
    assert coverage("主库切换已完成。", "主库的切换已完成。") == 1.0
    assert coverage("生产环境出错时会自动回滚。", "关闭生产环境的自动回滚。") == pytest.approx(8 / 12)
    # A lone character counts when the passage contains it anywhere.
    assert coverage("超时是 8 秒。", "超时放宽到 8 秒放行") == 1.0


def test_coverage_weighs_latin_terms_like_a_word():
    # Seven CJK characters (公开, 限流, 每分钟) and "API" with weight 2; the passage lacks "API".
    assert coverage("公开 API 限流是每分钟。", "公开限流是每分钟。") == pytest.approx(7 / 9)


def test_opposing_term_counts_as_covered():
    # 功能 and A (weight 2) are found, 批准 is opposed by 拒绝, 已 is missing.
    assert coverage("功能A已批准", "产品经理拒绝了功能A") == pytest.approx(6 / 7)
    assert judge("功能A已批准", "产品经理拒绝了功能A").relation == "contradicts"


# --- negation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("passage, cue", [
    ("支付服务不再重试。", "不再"), ("支付服务禁止重试。", "禁止"), ("支付服务没有重试。", "没有"),
    ("支付服务未重试。", "未"), ("支付服务无重试。", "无"), ("支付服务并非重试。", "非"),
    ("支付服务别重试。", "别"), ("支付服务勿重试。", "勿"), ("支付服务不得重试。", "不得"),
])
def test_negation_cues(passage, cue):
    assert negation(passage) == cue
    judgment = judge("支付服务会重试。", passage)
    assert judgment.relation == "contradicts" and judgment.rationale == "same subject, but only one side is negated"


@pytest.mark.parametrize("passage", [
    "未来团队重点是加载速度。", "团队重点非常明确：加载速度。", "无论白天夜间，团队重点是加载速度。",
    "团队重点不仅是加载速度。", "团队重点按级别区分：加载速度。", "团队重点非得是加载速度不可。",
    "团队重点不得不是加载速度。",
])
def test_words_and_double_negatives_that_do_not_negate(passage):
    assert not has_negation(passage)


def test_negation_only_counts_in_a_clause_about_the_other_side():
    claim = "研发预算是1200万元。"
    passage = "研发预算总额为1200万元，其中云资源费用不超过400万元。"
    assert has_negation(passage) and negation(passage, claim) is None
    assert judge(claim, passage).relation == "supports"
    assert negation("研发预算总额为1200万元，研发预算不再追加。", claim) == "不再"


# --- modality ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim, passage, cues", [
    ("支付服务对所有接口都会重试。", "支付服务仅对幂等接口重试。", ("所有", "仅")),
    ("只有管理员可以导出报表。", "任何成员都可以导出报表。", ("只有", "任何")),
    ("上线前必须通过隐私评审。", "上线前无需通过隐私评审。", ("必须", "无需")),
    ("上线前不必通过隐私评审。", "上线前非得通过隐私评审不可。", ("不必", "非得")),
])
def test_opposite_modality_contradicts(claim, passage, cues):
    assert modality_conflict(claim, passage) == cues
    judgment = judge(claim, passage)
    assert judgment.relation == "contradicts"
    assert judgment.rationale == f"opposite modality: claim '{cues[0]}' vs source '{cues[1]}'"


@pytest.mark.parametrize("claim, passage", [
    ("上线前必须通过隐私评审。", "上线前非得通过隐私评审不可。"),  # both oblige
    ("所有接口都会重试。", "所有接口都会重试。"),
    ("所有接口都会重试。", "接口都会重试，仅限内网调用。"),  # 仅 is about something else
])
def test_same_or_unrelated_modality_does_not_contradict(claim, passage):
    assert modality_conflict(claim, passage) is None


@pytest.mark.parametrize("claim, passage, cues", [
    ("只有管理员可以导出报表。", "不只有管理员可以导出报表。", ("只有", "不只有")),
    ("只能管理员可以导出报表。", "不只能管理员可以导出报表。", ("只能", "不只能")),
    ("仅仅管理员可以导出报表。", "不仅仅管理员可以导出报表。", ("仅仅", "不仅仅")),
])
def test_negated_only_is_the_opposite_of_only(claim, passage, cues):
    assert modality_conflict(claim, passage) == cues
    judgment = judge(claim, passage)
    assert judgment.relation == "contradicts"
    assert judgment.rationale == f"opposite modality: claim '{cues[0]}' vs source '{cues[1]}'"


def test_not_only_and_all_are_the_same_pole():
    assert modality_conflict("所有成员可以导出报表。", "不仅仅管理员可以导出报表。") is None


def test_correlative_not_only_is_not_a_modality_cue():
    claim = "支付服务对所有接口都会重试。"
    passage = "支付服务不仅会对接口重试，还会告警。"
    assert modality_conflict(claim, passage) is None
    assert "仅" not in (modality_conflict(passage, claim) or ())
    assert judge(claim, passage).relation != "contradicts"
    # 归还 does not start the next clause, so it is not the correlative 还.
    assert modality_conflict("只有管理员可以归还报表。", "不只有管理员可以归还报表。") == ("只有", "不只有")


def test_freeze_claim_stays_one_sided_negation():
    claim = "封版期间上线不仅要是 P0 修复，而且要 CTO 审批。"
    passage = "期间仅允许 P0 级修复上线，由当周值班经理审批即可，无需 CTO 签字。"
    assert modality_conflict(claim, passage) is None
    judgment = judge(claim, passage)
    assert judgment.relation == "contradicts"
    assert judgment.rationale == "same subject, but only one side is negated"


def test_fei_buke_followed_by_a_word_still_negates():
    passage = "非技术人员不可进入机房。"
    assert has_negation(passage)
    assert modality_conflict("必须进入机房。", passage) is None
    judgment = judge("技术人员可以进入机房。", passage)
    assert judgment.relation == "contradicts"
    assert judgment.rationale == "same subject, but only one side is negated"
    # Clause-final 非…不可 still obliges, and the cue does not repeat the interior.
    assert not has_negation("上线前非通过不可。")
    assert modality_conflict("上线前不必评审。", "上线前非通过不可。") == ("不必", "非…不可")
    # 非技术人员 shares the n-grams of 技术人员. With 不可进入 read as negation, this is a candidate
    # contradiction. No dictionary treats the 非 prefix as "non-".
    residual = judge("必须进入机房。", "非技术人员不可进入机房。")
    assert residual.relation == "contradicts"
    assert residual.rationale == "same subject, but only one side is negated"


def test_fei_buke_cue_drops_untrusted_interior():
    # The controls sit in the idiom, and the two sides still share enough terms to be judged.
    passage = "上线前非完成隐私评审\u202e\x1b不可。"
    judgment = judge("上线前不必完成隐私评审。", passage)
    assert judgment.relation == "contradicts"
    assert judgment.rationale == "opposite modality: claim '不必' vs source '非…不可'"
    assert "\u202e" not in judgment.rationale and "\x1b" not in judgment.rationale


# --- claims and the examples ------------------------------------------------------------------------------

def test_a_bare_quantity_is_not_a_claim(tmp_path):
    # As with "3 months." in English, a quantity is a number, not one of the two content terms a claim needs.
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- 三个月。\n- 3 months.\n- 应用日志只保存三个月。\n- 第四季度不招前端了。\n", encoding="utf-8")
    claims, _ = parse_memory(memory)
    assert [c.text for c in claims] == ["应用日志只保存三个月。", "第四季度不招前端了。"]
