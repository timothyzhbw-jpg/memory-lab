"""The lexical verifier's rules, one sentence each in docs/lexical-rules.md, and what the audit does with them."""
import json
import time
from datetime import date
from pathlib import Path

import pytest

from conftest import write_text_exact
from memfalsify import audit
from memfalsify.audit import AuditReport, weighed
from memfalsify.corpus import MAX_LINE, left_out_reason, scan, source_kind
from memfalsify.report import to_markdown
from memfalsify.text import named_conflict, named_values, quantities
from memfalsify.verify import Judgment, LexicalVerifier, strength

judge = LexicalVerifier().judge


def relation(claim, passage):
    return judge(claim, passage).relation


# --- named values ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "max_steps: 500", "max_steps (default: 500)", "max_steps (default 500)",
    "max_steps: int = 500", "max_steps: int = Field(default=500, description='Steps.')",
    '"max_steps": 500,', "| max_steps | 500 |", "| `max_steps` | int | 500 | Steps |", "`max_steps` (default: `500`)",
    "max_steps defaults to 500", "`max_steps` is 500", "MAX_STEPS: 500", "max_steps: 500.0 # plenty",
])
def test_named_value_forms(source):
    assert relation("max_steps: 100", source) == "contradicts"
    assert judge("max_steps: 100", source).rationale.startswith("same setting max_steps: claim 100 vs source 500")


@pytest.mark.parametrize("key", ["max_steps", "max-steps", "maxSteps", "agent.max_steps", "`steps`"])
def test_a_key_reads_as_an_identifier(key):
    bare = key.strip("`")
    assert bare.lower() in named_values(f"{key}: 3")


@pytest.mark.parametrize("text", ["Steps: 3", "Note: 3 things", "version 3", "| Neo4j HTTP | 8474 |", "'width': 3",
                                  "see README: 3"])
def test_a_plain_word_is_not_a_key(text):
    assert named_values(text) == {}


@pytest.mark.parametrize("source", ["max_steps = 500", "max_steps=500", "--max_steps 500", "--max_steps=500"])
def test_a_value_set_somewhere_counts_against_a_plain_statement(source):
    assert judge("max_steps: 100", source).rationale == "set differently max_steps: claim 100 vs source 500"


def test_a_value_merely_set_needs_the_rest_of_the_line_too():
    assert relation("Wrap it in a <span className='hidden'> for screen readers.", "<div className='wide'>Plans</div>") != "contradicts"
    assert relation("max_steps: 100", "max_steps = 500 in the agent settings") == "contradicts"


def test_a_flag_is_a_key():
    assert relation("Run with --workers 4", "Run with --workers=8") == "contradicts"
    assert "workers" in named_values("--workers 4")


@pytest.mark.parametrize("claim, source", [("timeout_ms = 100", "timeout_ms = 200"), ("timeout_ms=100", "timeout_ms=200"),
                                           ("--timeout-ms 100", "--timeout-ms 200")])
def test_two_values_that_are_merely_set_say_less(claim, source):
    assert judge(claim, source).rationale == f"set differently {named_conflict(claim, source).key}: claim 100 vs source 200"
    assert weighed(judge(claim, source), "docs/a.toml", claim, source).relation == "contradicts"
    assert weighed(judge(claim, source), "examples/a.md", claim, source).relation == "unrelated"
    assert weighed(judge(claim, source), "src/a.py", claim, source).relation == "unrelated"


@pytest.mark.parametrize("claim, source", [
    ("max_steps: 100", "max_steps: 100"),
    ("max_steps: 100", "max_steps: 100.0"),
    ("max_steps: 1000", "max_steps: 1_000"),
    ("max_steps: 1000", "max_steps: 1,000"),
    ("use_vision: true", "use_vision: True"),
    ('mode_name: "fast"', "mode_name: 'FAST'"),
    ("max_steps: 100", "MAX_STEPS = 100"),
    ("version_pin: 3.12", 'version_pin: "3.12.0"'),
    ("wait_time: 500ms", "wait_time: 1s"),  # units the text does not convert
])
def test_same_value_or_cannot_tell(claim, source):
    assert named_conflict(claim, source) is None


@pytest.mark.parametrize("source", ["log_format: str | None = None", "log_format: None", "log_format (default: null)",
                                    '"log_format": null'])
def test_a_null_is_no_value(source):
    assert named_conflict("log_format (default: 'plain')", source) is None


@pytest.mark.parametrize("source", ["open_browser=False", "app = App(name='x', open_browser=False)",
                                    "`open_browser=False`", "open_browser = False", "--open_browser False"])
def test_a_value_set_somewhere_does_not_contradict_a_stated_default(source):
    assert named_conflict("open_browser (default: True)", source) is None
    assert named_conflict("open_browser: True", source) is not None


def test_conditions_and_comparisons_bind_nothing():
    assert named_values("if use_vision is True and x:") == {}
    assert named_values("if max_steps == 3:") == {}
    assert named_values("max_steps != 3") == {}


@pytest.mark.parametrize("claim, source", [
    ("MAX_RETRIES: -1", "MAX_RETRIES: int = 1"),
    ("`max_retries: -1` disables the limit.", "`max_retries: 1` disables the limit."),
    ("offset_ms: -5", "offset_ms: 5"),
])
def test_a_negative_value_is_not_its_magnitude(claim, source):
    assert judge(claim, source).relation == "contradicts"


@pytest.mark.parametrize("claim, source", [("offset_ms: -5", "offset_ms: -5.0"), ("offset_ms: 5", "offset_ms: +5"),
                                           ("offset_ms: 0", "offset_ms: -0")])
def test_a_sign_that_changes_nothing(claim, source):
    assert named_conflict(claim, source) is None


@pytest.mark.parametrize("claim, source", [
    ("`max_size: 512MB` for uploads.", "`max_size: 512GB` for uploads."),
    ("`session_timeout: 30m` in config.yaml.", "`session_timeout: 30s` in config.yaml."),
    ("`max_size: 512MB` for uploads.", "`max_size: 512MiB` for uploads."),
])
def test_the_same_digits_in_another_unit_are_another_value(claim, source):
    assert judge(claim, source).relation == "contradicts"


@pytest.mark.parametrize("claim, source", [
    ("wait_time: 500ms", "wait_time: 1s"),  # units the text does not convert
    ("max_size: 512MB", "max_size: 30s"),  # not the same kind of thing
    ("max_size: 512MB", "max_size: 512"),  # one side names no unit
    ("wait_time: 5h", "wait_time: 5x"),  # not a unit
])
def test_units_the_text_cannot_compare_stay_apart(claim, source):
    assert named_conflict(claim, source) is None


def test_another_key_is_another_setting():
    assert relation("read_timeout (default: 120): Timeout in seconds for each read",
                    "write_timeout: int = 60  # Timeout in seconds for each write") != "contradicts"
    assert relation("max_parts (default: 3): Largest number of parts in an upload",
                    "max_tries: Largest number of tries in an upload (default: 5).") != "contradicts"


def test_named_value_needs_no_other_shared_words():
    long_claim = "max_steps (default: 100): Largest number of steps a worker can take before it gives up on a job."
    assert relation(long_claim, "max_steps: int = 500,") == "contradicts"


# --- numbers ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim, source", [
    ("The rate limit is 100 requests per minute.", "The rate limit is 60 requests per minute."),
    ("The cache runs on Redis 6.", "The cache runs on Redis 7."),
    ("The main database runs PostgreSQL 15.", "The main database runs PostgreSQL 16 since the September upgrade."),
    ("Raw GPS pings are deleted after 90 days.", "Raw GPS pings: deleted after 30 days."),
    ("Backups run every 6 hours and are kept for 35 days.", "Backups run every 6 hours and are kept for 14 days."),
    ("The form can hold 3 fields at once.", "Largest batch (e.g., hold 5 form fields at once)"),
    ("Production is running release 2.3.2.", "On 2026-05-20 the production release became release 2.3.1."),
    ("Shipped on 2026-10-11 to everyone.", "Shipped on 2026-10-12 to everyone."),
])
def test_different_numbers_on_the_same_word(claim, source):
    assert judge(claim, source).rationale.startswith("different values")


@pytest.mark.parametrize("claim, source", [
    ("Formatter, line length 88.", "const line = `${lines.length + 1}."),
    ("Formatter, line length 88.", "if (lines.length === 0) return"),
    ("The client library runs on Python 3.9.", "Requests go through the client library ([#4805](https://x.test/pull/4805))."),
    ("The client library runs on Python 3.9.", "Fixed the client library runs (#7326)."),
    ("Write scripts as RFC 723 scripts with inline metadata.",
     "Scripts with [inline metadata](https://rfc.example/rfc-0723/)."),
    ("Internal helpers in JS (src/setup/40_setup.js, src/lib/)", "src/lib/setup.ts: 23"),
    ("The provider tag is my-sdk-v2.0.6 today.", "The provider tag is 3 today."),
    ("The review stage starts at 14:21 in the morning.", "The review stage starts at 3 in the morning."),
    ("The 2026-07-30 tile outage was caused by an expired TLS certificate.",
     "14:21 Investigated an expired TLS certificate on the staging tile server;"),
    ("Pinned at RFC 0723 for scripts.", "Pinned at RFC 723 for scripts."),
])
def test_numbers_that_do_not_count(claim, source):
    assert relation(claim, source) != "contradicts"


def test_quantities_read_numbers_on_their_own():
    values = {q.value for q in quantities("rfc-0723 v2.0.6 my-sdk-v2.0.6 40_setup.js #7326 14:21 lib@7.5.22 x/9 8 fields")}
    assert values == {"2.0.6", "8"}
    assert {q.value for q in quantities("0723 pages and 2.0 pages and 3.10 pages")} == {"723", "2", "3.10"}


def test_a_number_bound_to_a_key_is_only_compared_by_key():
    assert quantities("max_steps (default: 100)") == ()
    assert quantities('"line": 2, "character": 49') == ()


def test_a_number_in_a_terminal_escape_does_not_hide():
    assert relation("The public API allows 100 requests per minute.",
                    "The public API allows \x1b[31m60 requests per minute.") == "contradicts"


@pytest.mark.parametrize("claim, source, values", [
    ("The build uses Node 18.x.", "The build uses Node 20.x.", "['18.x'] vs source ['20.x']"),
    ("Runtime is Python 3.x only.", "Runtime is Python 2.x only.", "['3.x'] vs source ['2.x']"),
    ("Rate limits are 100/min.", "Rate limits are 200/min.", "['100'] vs source ['200']"),
    ("The limit is 1,000 requests per minute.", "The limit is 2,000 requests per minute.", "['1000'] vs source ['2000']"),
    ("The limit is 1,000 requests per minute.", "The limit is 900 requests per minute.", "['1000'] vs source ['900']"),
])
def test_numbers_in_the_forms_people_write_them(claim, source, values):
    assert judge(claim, source).rationale == f"different values: claim {values}"


@pytest.mark.parametrize("claim, source", [
    ("The limit is 1,000 requests per minute.", "The limit is 1000 requests per minute."),
    ("The build uses Node 18.x.", "The build uses Node 18.20."),
    ("The build uses Node 18.x.", "The build uses Node 18."),
    ("The build uses Node 18.20.", "The build uses Node 18.x today."),
    ("Rate limits are 100/min.", "Rate limits are 100/min."),
])
def test_one_value_written_two_ways_is_no_conflict(claim, source):
    assert relation(claim, source) != "contradicts"


def test_what_only_looks_like_a_rate_or_a_wildcard_is_not_read_as_one():
    assert {q.value for q in quantities("s3/bucket 4/s3 10/min/foo 24/7 2.xz 1.x_y 8 fields")} == {"8"}


@pytest.mark.parametrize("claim, source", [
    ("Use 2 spaces for indentation in YAML files.", "Use 4 spaces for indentation in Python files."),
    ("The API returns 200 on success.", "The API returns 404 when the resource is missing."),
    ("The maximum upload size is 10 MB for images.", "The maximum upload size is 50 MB for videos."),
])
def test_two_numbers_each_with_a_case_of_its_own_do_not_conflict(claim, source):
    assert relation(claim, source) != "contradicts"


@pytest.mark.parametrize("claim, source", [
    ("Use 2 spaces for indentation in YAML files.", "Use 4 spaces for indentation in YAML files."),
    ("The gateway timeout is 1500 ms.", "The gateway timeout is 30 seconds."),
    ("The maximum upload size is 10 MB.", "The maximum upload size is 50 MB for videos."),
])
def test_a_case_named_on_one_side_only_still_conflicts(claim, source):
    assert relation(claim, source) == "contradicts"


# --- units -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim, source", [
    ("A checkout write returns within 2 minutes.", "A checkout write must return within 2 seconds."),
    ("Search indexes occupy 512 GB on the hot tier.", "Search indexes occupy 512 GiB on the hot tier."),
    ("La confirmación de un pedido puede tardar hasta 2 minutos.", "La confirmación de un pedido debe responder en 2 segundos."),
    ("下单接口的返回时限是 2 分钟。", "下单接口必须在 2 秒内返回。"),
])
def test_same_number_other_unit(claim, source):
    assert judge(claim, source).rationale.startswith("different units")


def test_units_of_another_kind_are_not_compared():
    assert relation("The cache holds 5 GB for 3 days.", "The cache holds 5 GB for 3 days.") == "supports"
    assert not judge("Disks hold 5 GB of logs.", "Disks hold 5 days of logs.").rationale.startswith("different units")


# --- opposing terms --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim, source", [
    ("Feature A is approved.", "Product manager rejects Feature A."),
    ("Route optimization v2 is enabled for all Enterprise customers.",
     "Route optimization v2 stays disabled by default for Enterprise accounts and is switched on per account."),
    ("Self-hosting Kafka on EC2 was approved as the replacement for RabbitMQ.",
     "Option A (self-hosted Kafka on EC2) was rejected: nobody on the team has run Kafka in production."),
    ("Pyright's include list excludes src.", 'include = ["src"]'),
])
def test_opposing_terms_on_the_same_subject(claim, source):
    assert judge(claim, source).rationale.startswith("opposing terms")


@pytest.mark.parametrize("claim, source", [
    ("Keep the required build tools around for the release.", "Skip optional packages that do not match the platform."),
    ("Need to add a tool?", "build: remove tools/build.py;"),
    ("Public or private notes", '"access": "public"'),
    ("When both allowed_hosts and blocked_hosts are set, allowed_hosts takes precedence.",
     "profile = Profile(blocked_hosts=hosts, headless=True)"),
    ("blocked_hosts: Keep requests away from certain hosts.", "`allowed_hosts`: Keep requests on certain hosts:"),
    ("Open a pull request that links an issue.", "Every pull request must link an issue using `Closes #123`."),
])
def test_opposing_terms_that_do_not_count(claim, source):
    assert relation(claim, source) != "contradicts"


# --- negation --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim, source", [
    ("Production deploys are never allowed on Fridays.", "Production deploys on Fridays are allowed until 14:00 UTC."),
    ("Self-serve signup currently requires a credit card.", "2026-09-01 — Self-serve signup no longer asks for a credit card."),
    ("Billing invoice renders must not be retried.",
     "A billing invoice render may be retried at most once, and only if the first attempt returned HTTP 503."),
    ("Live tests need the QUAYSIDE_API_KEY environment variable.",
     "Live tests authenticate with `QUAYSIDE_SANDBOX_KEY`, not with `QUAYSIDE_API_KEY`."),
    ("Deployments always go through the staging cluster.",
     "A hotfix was deployed directly to production, skipping the staging cluster."),
])
def test_negation_on_the_same_subject(claim, source):
    assert judge(claim, source).rationale == "same subject, but only one side is negated"


@pytest.mark.parametrize("claim, source", [
    ("memlab-ui is a web page", "memlab-ui runs a web page without learning the command line"),
    ("memlab-ui is a web page", "| memlab-ui web page | none (new) |"),
    ("Always use yarn instead of npm for dependency management", "Use `yarn` for dependency management, never `npm`"),
    ("Do not refactor unrelated code.", "Unrelated code gets a refactor in its own pull request."),
    ("Never add a test plan to the description.", "description: Generate a test plan for it"),
    ("Please do not open pull requests for typos.", "Prior to opening pull requests for typos, read this."),
    ("The path is not_found in the cache layer", "The path is in the cache layer"),
    ("Run the build with --no-cache for each release", "Run the build for each release"),
    ("save_encoding (default: 'utf-8'): Encoding for saved notes", "save_encoding: str | None = 'utf-8',"),
    ("The user data dir holds a temp copy.", "user data dir is not None and holds a temp copy."),
    ("Core libraries:", "The core `@acme/editor` library has no built-in tools."),
])
def test_negation_that_does_not_count(claim, source):
    assert relation(claim, source) != "contradicts"


@pytest.mark.parametrize("text", ["max_steps: " + "9" * 5000, "a_b " * 3000, "key: " * 3000, "not " * 3000, "| " * 3000,
                                  "1 " * 3000, "x" * 20000, "a-" * 3000, "max_steps=1, " * 1000, "no, " * 450,
                                  "enabled a, " * 150, "1 day, " * 250])
def test_hostile_text_gets_a_verdict(text):
    assert judge(text, text + "!").relation in ("supports", "contradicts", "unrelated")
    assert judge("max_steps: 100", text).relation in ("supports", "contradicts", "unrelated")
    assert judge("disabled a b", text).relation in ("supports", "contradicts", "unrelated")


SECONDS = 5  # far more than any of these takes now; each took 15 seconds or much more when it grew with the square


@pytest.mark.parametrize("claim, passage", [
    ("The cache holds " + "a_b=1 " * 3000, "The cache holds " + "a_b=2 " * 3000),  # each binding compared with each
    ("The cache holds " + "".join(f"a_b={n} " for n in range(3000)), "The cache holds " + "".join(f"a_b={n + 5000} " for n in range(3000))),
    ("The cache a_b:" + " " * 32_000 + "is large.", "The cache a_b: is large today."),  # key: type = value
    ("The cache is not large.", "The cache a_b:" + " " * 32_000 + "is large."),
    ("The cache is " + "not " * 12_000, "The cache is large."),  # what each cue reaches
    ("The cache is large.", ("the cache is large " + "not " * 235 + "\n") * 12),
])
def test_hostile_text_takes_time_in_proportion_to_its_length(claim, passage):
    started = time.perf_counter()
    assert judge(claim, passage).relation in ("supports", "contradicts", "unrelated")
    assert time.perf_counter() - started < SECONDS


def test_one_key_keeps_only_a_few_bindings():
    text = "".join(f"a_b={n} " for n in range(1000))
    assert len(named_values(text)["a_b"]) == 8
    assert named_values("a_b=1 " * 1000)["a_b"] == named_values("a_b=1")["a_b"]


def test_many_lines_of_spaces_after_a_key_take_time_in_proportion_to_their_length(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- The cache is large for the a_b setting today.\n"})
    write_tree(tmp_path / "evidence", {"a.md": "".join(f"a_b{n}:" + " " * 900 + "the cache is large for the a_b setting\n\n"
                                                       for n in range(300))})
    started = time.perf_counter()
    audit(tmp_path / "MEMORY.md", tmp_path / "evidence")
    assert time.perf_counter() - started < SECONDS


def test_a_key_and_type_with_spaces_still_bind():
    assert named_values("max_steps : int  = 5")["max_steps"][0].value == "5"
    assert named_values("max_steps: Optional[int]\t= 5")["max_steps"][0].value == "5"
    assert named_values("max_steps:\n  int = 5")["max_steps"][0].value == "5"


@pytest.mark.parametrize("one, other, reason", [
    ("Telemetry is disabled.", "Telemetry is enabled.", "opposing terms"),
    ("Authentication is required.", "Authentication is optional.", "opposing terms"),
    ("The repo is public.", "The repo is private.", "opposing terms"),
    ("Redis is required.", "Redis is not required.", "same subject"),
    ("We use Docker.", "We do not use Docker.", "same subject"),
])
def test_a_short_statement_and_its_opposite(one, other, reason):
    assert judge(one, other).rationale.startswith(reason)
    assert judge(other, one).rationale.startswith(reason)


@pytest.mark.parametrize("claim, source", [
    ("Telemetry is disabled for this.", "Sentry is enabled for this."),  # another subject
    ("Redis is required.", "Postgres is not required."),
    ("We use Docker.", "We do not use Docker Compose."),
])
def test_a_short_statement_about_something_else_is_no_opposite(claim, source):
    assert relation(claim, source) != "contradicts"


def test_two_short_lines_of_one_file_that_oppose_each_other_conflict(tmp_path):
    write_tree(tmp_path, {"CLAUDE.md": "- Telemetry is disabled.\n- Telemetry is enabled.\n"})
    report = audit(tmp_path / "CLAUDE.md")
    assert all("internal_conflict" in {issue.kind for issue in finding.issues} for finding in report.findings)


@pytest.mark.parametrize("prohibition, plain", [
    ("Do not use npm in this repository.", "Use npm in this repository."),
    ("Never commit directly to main.", "Commit directly to main."),
    ("Don't commit secrets to the repo.", "Commit secrets to the repo."),
    ("Do not run database migrations in production.", "Run database migrations in production on Fridays."),
    ("Never use `yarn` here.", "Use `yarn` here."),
])
def test_a_prohibition_and_its_opposite(prohibition, plain):
    assert judge(prohibition, plain).rationale == "same subject, but only one side is negated"
    assert judge(plain, prohibition).rationale == "same subject, but only one side is negated"


@pytest.mark.parametrize("prohibition, plain", [
    ("Do not use npm in this repository.", "Do not use npm in this repository."),
    ("Do not use npm.", "Sessions use npm for caching and for building."),
    ("Never commit directly to main.", "Reviews commit directly to the release branch from main."),
    ("Never npm, never yarn.", "`npm` or `yarn`."),  # names and no statement
    ("Never check only one of them.", "To check for one:"),  # a label and no statement
    # The bar is strict on purpose. This one is a real conflict that it misses, and it is the same shape as an item of a
    # "Do NOT" list against the prose it came from (Use npm or yarn in TypeScript packages), which was all it found on the
    # measured repositories when the bar let it through.
    ("Do not run migrations in production.", "Run migrations in production after every deploy."),
])
def test_a_prohibition_says_little_about_a_line_that_only_shares_its_words(prohibition, plain):
    assert relation(prohibition, plain) != "contradicts"


def test_a_prohibition_finds_its_opposite_in_a_small_repository(tmp_path):
    write_tree(tmp_path, {"AGENTS.md": "- Do not use npm in this repository.\n",
                          "CONTRIBUTING.md": "Use npm in this repository.\n"})
    report = audit(tmp_path / "AGENTS.md", tmp_path)
    assert report.findings[0].status == "candidate_counterevidence"


def test_a_passage_the_rules_doubt_is_no_support_either():
    """What used to be a contradiction and no longer is must not turn into support."""
    for claim, passage in (("Do not use Redis for caching.", "Sessions use Redis for caching."),
                           ("The installer works without a key.", "The installer works with a key."),
                           ("Allowed origins list the staging host.", "Blocked origins list the staging host today."),
                           ("Python 3.9 runs the unit tests.", "Python 3.10 runs them, and the unit tests are slow.")):
        assert relation(claim, passage) != "supports", claim


# --- removal ---------------------------------------------------------------------------------------------------

def test_source_says_it_was_removed():
    claim = "| Queue backends | 3 | Alder, Birch, Cedar |"
    source = "**Note:** All external queue backends (Alder, Birch, Cedar) were subsequently removed in v2.0.0."
    assert judge(claim, source).rationale == "source says what the claim names was removed"


def test_removal_is_about_what_stands_before_the_words():
    assert relation("environment before constructing the client.",
                    "The configured key was previously dropped, so the client fell back to the environment at construction.") != "contradicts"
    assert relation("Remove the old graph store backends.", "Old graph store backends were removed in v2.") != "contradicts"


@pytest.mark.parametrize("claim, source", [
    ("The legacy CSV importer handles uploads.", "The legacy CSV importer was removed in version 2."),  # says more about it
    ("Legacy CSV importer plugin.", "The legacy CSV importer plugin is removed."),  # is, not only was
    ("The legacy CSV importer plugin handles uploads.", "The legacy CSV importer plugin has been deleted."),
])
def test_a_source_says_the_thing_a_claim_talks_about_is_gone(claim, source):
    assert judge(claim, source).rationale == "source says what the claim names was removed"


@pytest.mark.parametrize("claim, source", [
    ("The build temp file lives in the cache.", "The build temp file is removed after each run."),  # what happens each time
    ("The legacy CSV importer was dropped from the docs.", "The legacy CSV importer was removed in version 2."),  # says so itself
    ("We delete the legacy CSV importer soon.", "The legacy CSV importer was removed in version 2."),
    ("The new importer handles CSV uploads.", "The old importer was removed in version 2."),
])
def test_a_removal_that_does_not_speak_against_the_claim(claim, source):
    assert not judge(claim, source).rationale.startswith("source says")


# --- what kind of file a passage is in -------------------------------------------------------------------------

@pytest.mark.parametrize("path, kind", [
    ("tests/ci/test_x.py", "test"), ("src/foo_test.go", "test"), ("web/a.test.tsx", "test"), ("web/a.spec.ts", "test"),
    ("conftest.py", "test"), ("CHANGELOG.md", "changelog"), ("packages/web/CHANGELOG-v6.md", "changelog"),
    ("docs/changelog/sdk.mdx", "changelog"), ("Releases.md", "changelog"), ("BREAKING_CHANGES.md", "changelog"),
    ("examples/blog/README.md", "example"), ("skills/references/examples.md", "example"), ("src/app.py", None),
    ("docs/api.md", None), ("releases/search.md", None), ("latest/contest.md", None), ("docs/test_plan.md", None),
    # Code and data under tests/ show a case. A document there, or anything under a folder that only sounds like tests,
    # says how things are: a spec is what the code has to follow.
    ("tests/specs/a/__test__.jsonc", "test"), ("tests/data/a.toml", "test"), ("tests/a.txt", "test"), ("e2e/login.ts", "test"),
    ("spec/models/a_spec.rb", "test"), ("testing/helpers.go", "test"),
    ("tests/README.md", None), ("specs/rate-limits.md", None), ("spec/api.yaml", None), ("docs/testing/setup.md", None),
    ("e2e/README.md", None),
    # A file named just so is a changelog. One that goes on to name a subject is a document about it.
    ("HISTORY.rst", "changelog"), ("NEWS", "changelog"), ("CHANGES.txt", "changelog"), ("docs/news.md", "changelog"),
    ("release-notes-2.md", "changelog"), ("changelog.old.md", "changelog"),
    ("docs/history-retention.md", None), ("docs/news-feed.md", None), ("docs/changes-to-retention.md", None),
    ("docs/releases-process.md", None), ("src/history.py", None), ("scripts/changelog.ts", None),
])
def test_source_kind(path, kind):
    assert source_kind(path) == kind


@pytest.mark.parametrize("path", ["specs/rate-limits.md", "spec/api.yaml", "docs/testing/setup.md", "e2e/README.md",
                                  "tests/README.md", "docs/history-retention.md", "docs/news-feed.md",
                                  "docs/changes-to-retention.md", "docs/releases-process.md"])
def test_an_authoritative_document_can_contradict_wherever_it_sits(tmp_path, path):
    write_tree(tmp_path, {"MEMORY.md": "- The API rate limit is 100 requests per minute.\n"})
    write_tree(tmp_path / "evidence", {path: "The API rate limit is 500 requests per minute.\n"})
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence")
    assert report.findings[0].status == "candidate_counterevidence"
    assert report.findings[0].evidence[0].rationale == "different values: claim ['100'] vs source ['500']"


def contradiction(rationale):
    return Judgment(relation="contradicts", rationale=rationale, verifier="lexical")


@pytest.mark.parametrize("path, rationale, relation_after", [
    ("tests/test_a.py", "same setting x_y: claim 1 vs source 2", "unrelated"),
    ("CHANGELOG.md", "same setting x_y: claim 1 vs source 2", "contradicts"),
    ("examples/a.md", "same setting x_y: claim 1 vs source 2", "contradicts"),
    ("src/a.py", "same setting x_y: claim 1 vs source 2", "contradicts"),
    ("CHANGELOG.md", "different values: claim ['1'] vs source ['2']", "unrelated"),
    ("examples/a.md", "same subject, but only one side is negated", "unrelated"),
    ("tests/a.json", "opposing terms: claim 'a…' vs source 'b…'", "unrelated"),
    ("tests/a.md", "opposing terms: claim 'a…' vs source 'b…'", "contradicts"),
    ("CHANGELOG.md", "source says what the claim names was removed", "contradicts"),
    ("examples/a.md", "source says what the claim names was removed", "unrelated"),
    ("docs/a.md", "same subject, but only one side is negated", "contradicts"),
])
def test_weighed(path, rationale, relation_after):
    assert weighed(contradiction(rationale), path, "x_y: 1", "x_y: int = 2").relation == relation_after


@pytest.mark.parametrize("rationale", ["opposing terms: claim 'a…' vs source 'b…'", "different units: a",
                                       "same subject, but only one side is negated", "different values: a"])
def test_in_source_code_only_a_declared_value_counts(rationale):
    assert weighed(contradiction(rationale), "src/a.py", "A claim", "if not x: return 5").relation == "unrelated"


@pytest.mark.parametrize("source, relation_after", [("retry_limit: int = 5", "contradicts"), ("RETRY_LIMIT = 5", "contradicts"),
                                                     ("retry_limit = 5", "unrelated"), ("call(retry_limit=5)", "unrelated")])
def test_in_source_code_a_value_must_be_declared(source, relation_after):  # by the memory line too: see the next test
    claim = "retry_limit: 3"
    assert relation(claim, source) == "contradicts"
    assert weighed(judge(claim, source), "src/a.py", claim, source).relation == relation_after
    assert weighed(judge(claim, source), "docs/a.md", claim, source).relation == "contradicts"


def test_a_memory_line_that_only_sets_a_value_finds_nothing_in_code():
    assert weighed(judge('class_name = "a"', "class_name: 'b'"), "src/a.tsx", 'class_name = "a"', "class_name: 'b'").relation == "unrelated"


@pytest.mark.parametrize("path, source, relation_after", [
    # One object's field or one call's argument, as foo(key=5) is in Python.
    ("src/a.ts", "retry({ maxRetries: 5 })", "unrelated"),
    ("src/a.py", 'retry(**{"maxRetries": 5})', "unrelated"),
    ("examples/a.ts", "retry({ maxRetries: 5 })", "unrelated"),
    # A declaration, in code or an example of it, and anything in a document or a config file.
    ("src/a.ts", "maxRetries: number = 5", "contradicts"),
    ("src/a.py", "maxRetries: int = 5", "contradicts"),
    ("src/a.ts", "// maxRetries (default: 5)", "contradicts"),
    ("examples/a.ts", "maxRetries: number = 5", "contradicts"),
    ("docs/a.md", "retry({ maxRetries: 5 })", "contradicts"),
    ("config.yaml", "maxRetries: 5", "contradicts"),
])
def test_in_code_a_field_of_one_object_is_no_declared_value(path, source, relation_after):
    claim = "`maxRetries` defaults to 3."
    assert judge(claim, source).rationale.startswith("same setting maxretries: claim 3 vs source 5")
    assert weighed(judge(claim, source), path, claim, source).relation == relation_after


def test_a_call_site_in_code_leaves_no_flag_in_a_full_audit(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- `maxRetries` defaults to 3.\n"})
    write_tree(tmp_path / "evidence", {"src/a.ts": "retry({ maxRetries: 5 })\n"})
    assert audit(tmp_path / "MEMORY.md", tmp_path / "evidence").findings[0].status == "insufficient_evidence"


def test_both_sides_negating_the_same_word_agree():
    assert relation("Name it exactly session_id with type Session (not client: Client).",
                    "**Warning:** Name it `session_id: Session`, not `client: Client`.") != "contradicts"


def test_a_word_the_negated_text_also_states_plainly_is_not_negated():
    assert relation("Keep changes small and focused;", "Keep your changes small, don't do side changes in a PR.") != "contradicts"


def test_weighed_leaves_support_alone():
    support = Judgment(relation="supports", rationale="source restates the claim", verifier="lexical")
    assert weighed(support, "tests/test_a.py", "a", "b") is support


# --- what a directory scan reads -------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["vendor/a/b.md", "pkg/third_party/x.py", "thirdparty/x.py", "go/testdata/a.txt",
                                  "tests/fixtures/a.json", "web/__snapshots__/a.txt", "dist/app.min.js", "a/b.min.css",
                                  "a.snap", "logo.svg", "fix.patch", "uv.lock", "Cargo.lock", "pnpm-lock.yaml",
                                  "package-lock.json", "go.sum"])
def test_vendored_or_generated_paths(path):
    assert left_out_reason(path)


@pytest.mark.parametrize("path", ["docs/vendors.md", "src/fixture_helpers.py", "lockfile.md", "package.json", "a.md"])
def test_ordinary_paths(path):
    assert left_out_reason(path) is None


def write_tree(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_exact(path, text)


def test_scan_leaves_vendored_files_out_and_lists_them(tmp_path):
    write_tree(tmp_path, {"docs/a.md": "Real evidence here.\n", "vendor/lib/a.md": "Vendored text here.\n",
                          "tests/fixtures/a.md": "Fixture text here.\n", "logo.svg": "<svg>Logo text here.</svg>\n",
                          "uv.lock": "Lock text here.\n"})
    passages, skipped = scan(tmp_path)
    assert [p.span.path for p in passages] == ["docs/a.md"]
    assert {s.path: s.reason for s in skipped} == {
        "vendor": "vendored or generated directory: not read as evidence",
        "tests/fixtures": "vendored or generated directory: not read as evidence",
        "logo.svg": "vendored or generated file: not read as evidence",
        "uv.lock": "vendored or generated file: not read as evidence"}


def test_the_rule_is_about_paths_inside_the_evidence_root(tmp_path):
    """Pointing --evidence at such a folder is how a person says it does count."""
    write_tree(tmp_path, {"fixtures/a.md": "Fixture text here.\n"})
    passages, skipped = scan(tmp_path / "fixtures")
    assert [p.span.path for p in passages] == ["a.md"] and skipped == []


def test_scan_leaves_long_lines_out_and_says_how_many(tmp_path):
    long = "word " * 300
    text = f"Short one here.\n{long}\nAnother short one.\n{'x' * (MAX_LINE + 1)}\nA last one is {'y' * (MAX_LINE - 15)}\n"
    write_tree(tmp_path, {"notes.md": text})
    passages, skipped = scan(tmp_path)
    quotes = [p.span.quote for p in passages]
    assert quotes[:2] == ["Short one here.", "Another short one."] and len(quotes) == 3
    assert [p.span.line for p in passages] == [1, 3, 5]
    assert all(text[p.span.start:p.span.end] == p.span.quote for p in passages)
    assert [(s.path, s.reason) for s in skipped] == [("notes.md", "long lines: 2 over 1,000 characters not read as passages")]


def test_a_file_left_out_by_its_name_stays_out_wherever_the_root_is(tmp_path):
    write_tree(tmp_path, {"patches/api.patch": "The rate limit is 100 requests per minute.\n"})
    passages, skipped = scan(tmp_path / "patches")
    assert passages == [] and [s.path for s in skipped] == ["api.patch"]


DOCS = Path(__file__).resolve().parent.parent / "docs"
WILD = DOCS.parent / "benchmarks" / "memfalsify" / "wild"


@pytest.mark.parametrize("page, words", [
    (DOCS / "lexical-rules.md", ["--evidence patches", "1,719", "794", "79 prose paragraphs"]),
    (DOCS / "lexical-rules.zh-CN.md", ["--evidence patches", "1,719", "794", "79 个散文段落"]),
    (WILD / "README.md", ["1,719", "794", "ruff's `AGENTS.md`"]),
    (WILD / "README.zh-CN.md", ["1,719", "794", "ruff 的 `AGENTS.md`"]),
])
def test_the_pages_say_what_the_measurements_and_the_scan_say(page, words):
    text = page.read_bytes().decode("utf-8")
    assert all(word in text for word in words)
    assert "a re-wrapped paragraph keeps its IDs" not in text and "重新折行的段落 ID 不变" not in text


def test_a_file_named_by_a_review_still_reads(tmp_path):
    """Reviews quote a file directly: the scan's policy is about nominating evidence, not about what a person cites."""
    write_tree(tmp_path / "evidence", {"fixtures/a.md": "The limit is 60 requests.\n"})
    write_tree(tmp_path, {"MEMORY.md": "- The limit is 100 requests.\n"})
    claim_id = audit(tmp_path / "MEMORY.md").findings[0].memory_id
    write_tree(tmp_path, {"reviews.jsonl": json.dumps({"memory_id": claim_id, "relation": "contradicts",
                                                       "path": "fixtures/a.md", "quote": "The limit is 60 requests.",
                                                       "reviewer": "you"}) + "\n"})
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence", tmp_path / "reviews.jsonl")
    assert report.findings[0].status == "challenged"


# --- ranking ---------------------------------------------------------------------------------------------------

MEMORY = """# Facts

- The cache holds 100 entries for each tenant.
- retry_limit (default: 3): How many times a call may be retried by the client.
- The queue holds 40 jobs for each tenant.
- Nothing supports this line about penguins.
"""
EVIDENCE = {
    "docs/a.md": "The cache holds 200 entries for each tenant.\n"
                 "The queue holds 50 jobs for each tenant.\n"
                 "How many times a call may be retried by the client: 7 times.\n",
    "docs/b.md": "retry_limit: int = 5\n",
}


@pytest.fixture
def ranked(tmp_path) -> AuditReport:
    write_tree(tmp_path, {"MEMORY.md": MEMORY})
    write_tree(tmp_path / "evidence", EVIDENCE)
    return audit(tmp_path / "MEMORY.md", tmp_path / "evidence", as_of=date(2026, 11, 5))


def test_a_named_value_comes_first_within_a_status(ranked):
    flagged = [f.text for f in ranked.findings if f.status == "candidate_counterevidence"]
    assert flagged[0].startswith("retry_limit")
    assert flagged[1:] == ["The cache holds 100 entries for each tenant.", "The queue holds 40 jobs for each tenant."]
    assert ranked.findings[-1].status == "insufficient_evidence"


def test_the_strongest_reason_comes_first(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- Reports are never mailed to the billing team.\n"
                                       "- The cache holds 100 entries for each tenant.\n"})
    write_tree(tmp_path / "evidence", {"a.md": "Reports are mailed to the billing team on Mondays.\n"
                                               "The cache holds 200 entries for each tenant.\n"})
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence")
    assert [f.text for f in report.findings] == ["The cache holds 100 entries for each tenant.",
                                                 "Reports are never mailed to the billing team."]


def test_strength_ranks_only_what_it_knows():
    ranks = [strength(text) for text in ("same setting x: claim 1 vs source 2", "different units: a", "different values: a",
                                         "source says what the claim names was removed", "set differently x: claim 1 vs source 2",
                                         "opposing terms: a",
                                         "opposite modality: a", "same subject, but only one side is negated",
                                         "a model's words")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


def test_the_passage_that_names_the_key_is_found_and_comes_first(ranked):
    finding = next(f for f in ranked.findings if f.text.startswith("retry_limit"))
    assert named_conflict(finding.text, "retry_limit = 5", set_counts=False) is None
    assert finding.evidence[0].span.quote == "retry_limit: int = 5"
    assert finding.evidence[0].rationale == "same setting retry_limit: claim 3 vs source 5"


def test_the_order_is_deterministic(tmp_path, ranked):
    write_tree(tmp_path / "again", {"MEMORY.md": MEMORY})
    write_tree(tmp_path / "again" / "evidence", EVIDENCE)
    again = audit(tmp_path / "again" / "MEMORY.md", tmp_path / "again" / "evidence", as_of=date(2026, 11, 5))
    assert [f.memory_id for f in again.findings] == [f.memory_id for f in ranked.findings]
    assert [[e.span.location for e in f.evidence] for f in again.findings] == [
        [e.span.location for e in f.evidence] for f in ranked.findings]


def test_a_test_file_with_the_same_key_is_not_evidence_against_a_default(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- retry_limit (default: 3): How many times a call may be retried by the client.\n"})
    write_tree(tmp_path / "evidence", {"tests/test_retry.py": "retry_limit: int = 5\n"})
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence")
    assert report.findings[0].status == "insufficient_evidence"


def test_two_memory_lines_that_bind_a_key_differently_conflict(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- retry_limit: 3 in the client.\n- Billing notes say retry_limit: 5 applies.\n"})
    report = audit(tmp_path / "MEMORY.md")
    assert {i.kind for f in report.findings for i in f.issues} >= {"internal_conflict"}


def test_a_model_verifier_sees_the_same_passages_and_keeps_its_verdict(tmp_path):
    """The lexical policy on test files and code does not touch what a model says about them."""
    class Sure:
        name = "sure"

        def judge(self, claim, passage):
            return Judgment(relation="contradicts", rationale="a model read it", verifier=self.name)

    write_tree(tmp_path, {"MEMORY.md": "- The retry limit is three for the client.\n"})
    write_tree(tmp_path / "evidence", {"tests/test_retry.py": "The retry limit is five for the client.\n"})
    report = audit(tmp_path / "MEMORY.md", tmp_path / "evidence", verifier=Sure())
    assert report.findings[0].status == "candidate_counterevidence"


def test_skips_reach_the_report(tmp_path):
    write_tree(tmp_path, {"MEMORY.md": "- A claim about nothing much at all.\n"})
    write_tree(tmp_path / "evidence", {"vendor/a.md": "Text.\n", "a.svg": "<svg/>\n", "b.md": "x" * 1001 + "\n"})
    report = to_markdown(audit(tmp_path / "MEMORY.md", tmp_path / "evidence"))
    assert "`vendor`: vendored or generated directory" in report
    assert "`a.svg`: vendored or generated file" in report
    assert "`b.md`: long lines: 1 over 1,000 characters" in report
