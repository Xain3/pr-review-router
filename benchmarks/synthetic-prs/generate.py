"""Generate deterministic, fictional PR evidence and independent benchmark labels."""

import difflib
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from pr_review_router.assessment_rubric import (
    AssessmentRubric,
    CriterionAssessment,
    combine_assessments,
    load_assessment_rubric,
)
from pr_review_router.config import Policy
from pr_review_router.contracts import PullRequestEvidence
from pr_review_router.engine import assess_pr_text

ROOT = Path(__file__).resolve().parent


class CriterionResult(TypedDict, total=False):
    """Optional fields used by categorical and ordinal oracle results."""

    status: str
    explanation: str
    type: str
    score: int
    level: str
    is_trivial: bool
    sources: list[str]


@dataclass(frozen=True)
class Edit:
    """One complete synthetic file change and its commit message."""

    path: str
    before: str
    after: str
    message: str


@dataclass(frozen=True)
class Family:
    """A factual change reused across controlled PR-text variations."""

    identifier: str
    complexity: str
    title: str
    summary: str
    rationale: str
    testing: str
    limitations: str
    breaking: str
    incompatible: bool
    false_title: str
    false_summary: str
    partial_summary: str
    edits: tuple[Edit, ...]


FAMILIES = (
    Family(
        "guide-typo",
        "trivial",
        "docs(guide): remove a repeated word",
        "Replace 'Read the the guide' with 'Read the guide' in docs/usage.md.",
        "The repeated word interrupts the onboarding instructions and can confuse readers.",
        "Validation plan: render docs/usage.md and check the corrected sentence; "
        "no runtime code changes.",
        "Only this sentence is corrected; the rest of the guide has not been audited.",
        "None; this is a prose correction.",
        False,
        "docs(guide): replace the guide with a video",
        "Delete docs/usage.md and replace the written guide with a video link.",
        "",
        (
            Edit(
                "docs/usage.md",
                "# Usage\n\nRead the the guide before starting.\n",
                "# Usage\n\nRead the guide before starting.\n",
                "docs(guide): remove a repeated word",
            ),
        ),
    ),
    Family(
        "cli-help",
        "trivial",
        "fix(cli): correct the output option in help text",
        "Correct the help string from --ouput to --output in cli/help.js; "
        "the parser and option behavior are unchanged.",
        "Users copying the help example currently receive an unknown-option error.",
        "Validation plan: print the help string and confirm it matches the parser's "
        "existing --output option.",
        "This only changes help text; option parsing and validation are outside scope.",
        "None; the accepted option remains --output.",
        False,
        "feat(cli): rename the output option to --destination",
        "Rename the parser option to --destination and remove support for --output.",
        "",
        (
            Edit(
                "cli/help.js",
                "export const help = 'Usage: demo --ouput FILE';\n",
                "export const help = 'Usage: demo --output FILE';\n",
                "fix(cli): correct the output option in help text",
            ),
        ),
    ),
    Family(
        "readme-link",
        "trivial",
        "docs: correct the getting started link",
        "Fix the README link target from docs/geting-started.md to "
        "docs/getting-started.md without changing its label.",
        "The misspelled path leads new users to a missing documentation page.",
        "Validation plan: resolve the relative README link to the existing guide.",
        "Other README links and the guide's contents are outside scope.",
        "None; no public API or command changes.",
        False,
        "docs: move the getting started guide to the website",
        "Replace the relative README link with https://example.invalid/start.",
        "",
        (
            Edit(
                "README.md",
                "# Demo\n\n[Getting started](docs/geting-started.md)\n",
                "# Demo\n\n[Getting started](docs/getting-started.md)\n",
                "docs: correct the getting started link",
            ),
        ),
    ),
    Family(
        "empty-average",
        "non_trivial",
        "fix(stats): handle an empty average input",
        "Return 0 for an empty values list before division in src/stats.py, and "
        "add regression tests for empty and ordinary inputs.",
        "An empty batch currently raises ZeroDivisionError; the reporting contract "
        "requires a neutral numeric result for batches with no observations.",
        "Validation plan: run tests/test_stats.py for [] and [2, 4]; "
        "the expected results are 0 and 3 respectively.",
        "Non-numeric values and iterators remain unsupported; callers expecting an "
        "exception for an empty list will observe a behavior change.",
        "No signature change. Empty input now returns 0 instead of raising "
        "ZeroDivisionError, which may affect exception-based callers.",
        False,
        "fix(stats): return one for an empty average input",
        "Return 1 for an empty values list, and add tests expecting 1 for [].",
        "",
        (
            Edit(
                "src/stats.py",
                "def average(values):\n    return sum(values) / len(values)\n",
                "def average(values):\n    if not values:\n        return 0\n"
                "    return sum(values) / len(values)\n",
                "fix(stats): return zero for an empty average",
            ),
            Edit(
                "tests/test_stats.py",
                "",
                "from src.stats import average\n\n\n"
                "def test_average():\n    assert average([]) == 0\n"
                "    assert average([2, 4]) == 3\n",
                "test(stats): cover empty and ordinary inputs",
            ),
        ),
    ),
    Family(
        "cache-key",
        "non_trivial",
        "fix(cache): normalize keys for lookup",
        "Lowercase keys before cache lookup in src/cache.py and add a test showing "
        "that 'USER' and 'user' resolve to the same stored entry.",
        "Callers use different capitalization for keys, causing misses against a "
        "store whose keys are already lowercase.",
        "Validation plan: run tests/test_cache.py and check mixed-case lookups "
        "against a lowercase-key store.",
        "Only lookup is normalized; writers must still store lowercase keys. "
        "Stores containing distinct uppercase keys may see changed lookup results.",
        "No signature change; key matching becomes case-insensitive for lookup.",
        False,
        "fix(cache): uppercase keys for lookup",
        "Uppercase keys before lookup and test resolution against uppercase stored keys.",
        "",
        (
            Edit(
                "src/cache.py",
                "def lookup(store, key):\n    return store.get(key)\n",
                "def lookup(store, key):\n    return store.get(key.lower())\n",
                "fix(cache): lowercase lookup keys",
            ),
            Edit(
                "tests/test_cache.py",
                "",
                "from src.cache import lookup\n\n\n"
                "def test_lookup():\n    assert lookup({'user': 7}, 'USER') == 7\n"
                "    assert lookup({'user': 7}, 'user') == 7\n",
                "test(cache): cover mixed-case lookup keys",
            ),
        ),
    ),
    Family(
        "batch-limit",
        "non_trivial",
        "feat(batch): raise the batch limit to 100",
        "Increase MAX_BATCH_SIZE from 50 to 100 in src/batch.py and add boundary "
        "tests that accept 100 items and reject 101.",
        "The import worker supplies up to 100 items per page; the current limit "
        "rejects valid pages and forces an unnecessary split.",
        "Validation plan: run tests/test_batch.py for the 100-item boundary and "
        "the rejected 101-item case.",
        "No memory or throughput measurements have been taken; larger accepted "
        "batches may increase peak memory.",
        "None expected; batches previously accepted remain accepted.",
        False,
        "feat(batch): raise the batch limit to 200",
        "Increase MAX_BATCH_SIZE from 50 to 200 and test acceptance of 200 items.",
        "",
        (
            Edit(
                "src/batch.py",
                "MAX_BATCH_SIZE = 50\n\ndef accepts(items):\n"
                "    return len(items) <= MAX_BATCH_SIZE\n",
                "MAX_BATCH_SIZE = 100\n\ndef accepts(items):\n"
                "    return len(items) <= MAX_BATCH_SIZE\n",
                "feat(batch): raise the batch limit to 100",
            ),
            Edit(
                "tests/test_batch.py",
                "",
                "from src.batch import accepts\n\n\n"
                "def test_boundary():\n    assert accepts([0] * 100)\n"
                "    assert not accepts([0] * 101)\n",
                "test(batch): cover batch-size boundaries",
            ),
        ),
    ),
    Family(
        "token-compare",
        "non_trivial",
        "fix(auth): compare tokens in constant time",
        "Use hmac.compare_digest instead of == in src/auth.py, and add tests for "
        "matching and mismatching ASCII tokens.",
        "Ordinary equality may reveal the first differing position through timing; "
        "token validation should avoid that comparison leak.",
        "Validation plan: run tests/test_auth.py for equal and unequal tokens. "
        "These functional tests do not measure timing or prove side-channel resistance.",
        "The contract still requires ASCII strings of the supported type; this "
        "does not protect unrelated authentication logic or network timing.",
        "None for supported ASCII tokens; unsupported token types are outside the contract.",
        False,
        "fix(auth): compare tokens without case sensitivity",
        "Lowercase both tokens before equality comparison and test case-insensitive matches.",
        "",
        (
            Edit(
                "src/auth.py",
                "def valid_token(actual, expected):\n    return actual == expected\n",
                "import hmac\n\n\ndef valid_token(actual, expected):\n"
                "    return hmac.compare_digest(actual, expected)\n",
                "fix(auth): use constant-time token comparison",
            ),
            Edit(
                "tests/test_auth.py",
                "",
                "from src.auth import valid_token\n\n\n"
                "def test_tokens():\n    assert valid_token('abc', 'abc')\n"
                "    assert not valid_token('abc', 'abd')\n",
                "test(auth): cover matching and mismatching tokens",
            ),
        ),
    ),
    Family(
        "csv-export",
        "non_trivial",
        "feat(export): add CSV serialization",
        "Add csv_text(rows) in src/export.py using csv.writer with LF line endings, "
        "and add tests for comma-containing fields and empty input.",
        "Downstream spreadsheet imports require delimited text; naive joining "
        "would corrupt fields that contain commas.",
        "Validation plan: run tests/test_export.py for a comma-containing field "
        "and an empty row list.",
        "The helper returns an in-memory string and adds no header automatically; "
        "large streaming exports and spreadsheet formula escaping are outside scope.",
        "None; this adds a new helper without replacing an existing API.",
        False,
        "feat(export): add JSON serialization",
        "Add a JSON serializer that returns a JSON array for rows and [] for empty input.",
        "Add csv_text(rows) using csv.writer with LF line endings.",
        (
            Edit(
                "src/export.py",
                "",
                "import csv\nimport io\n\n\n"
                "def csv_text(rows):\n    output = io.StringIO()\n"
                "    csv.writer(output, lineterminator='\\n').writerows(rows)\n"
                "    return output.getvalue()\n",
                "feat(export): add CSV serialization",
            ),
            Edit(
                "tests/test_export.py",
                "",
                "from src.export import csv_text\n\n\n"
                "def test_csv():\n    assert csv_text([['a,b', 'c']]) == '\"a,b\",c\\n'\n"
                "    assert csv_text([]) == ''\n",
                "test(export): cover CSV quoting and empty input",
            ),
        ),
    ),
    Family(
        "api-field",
        "non_trivial",
        "feat(api)!: rename the user response field",
        "Replace the JSON response key name with full_name in src/users.py and "
        "update docs/api.md to show the new response shape.",
        "API consumers confuse the existing name field with a login identifier; "
        "the response needs a distinct label for the person's full name.",
        "Validation plan: call user_response('Ada') and compare the returned key "
        "with the response example in docs/api.md.",
        "No compatibility alias or migration period is provided; existing consumers "
        "must update their response parsing.",
        "BREAKING CHANGE: the response removes name and exposes full_name instead; "
        "clients must read full_name.",
        True,
        "style(api): reformat the user response without changing fields",
        "Reformat user_response without changing the response keys or updating the API docs.",
        "",
        (
            Edit(
                "src/users.py",
                "def user_response(name):\n    return {'name': name}\n",
                "def user_response(name):\n    return {'full_name': name}\n",
                "feat(api)!: rename the response field to full_name",
            ),
            Edit(
                "docs/api.md",
                '# User response\n\nExample: {"name": "Ada"}\n',
                '# User response\n\nExample: {"full_name": "Ada"}\n',
                "docs(api): update the user response example",
            ),
        ),
    ),
    Family(
        "legacy-config",
        "non_trivial",
        "chore(config)!: remove the legacy configuration",
        "Delete config/legacy.toml and remove its setup instructions from docs/setup.md; "
        "config/default.toml remains the documented configuration.",
        "Two documented configuration entrypoints cause operators to select an "
        "obsolete file that is no longer maintained.",
        "Validation plan: inspect the remaining setup instructions and check that "
        "config/default.toml is the only documented configuration entrypoint.",
        "No automatic migration is included; scripts referencing the removed file "
        "must be updated manually.",
        "BREAKING CHANGE: config/legacy.toml is removed; deploy scripts must use "
        "config/default.toml.",
        True,
        "chore(config): update defaults in the legacy configuration",
        "Keep config/legacy.toml and change its timeout_seconds from 10 to 20; "
        "leave config/default.toml and setup instructions unchanged.",
        "",
        (
            Edit(
                "config/legacy.toml",
                "timeout_seconds = 10\nretries = 2\n",
                "",
                "chore(config)!: remove the legacy configuration file",
            ),
            Edit(
                "docs/setup.md",
                "# Setup\n\nUse config/legacy.toml for older deployments.\n"
                "Use config/default.toml for new deployments.\n",
                "# Setup\n\nUse config/default.toml for all deployments.\n",
                "docs(config): remove legacy setup instructions",
            ),
        ),
    ),
)

# Neutral `chore` titles and facts-only text avoid revealing the reference level. Adjacent
# pairs share a file shape: comment-typo/rename-local (0/1), extract-constant/change-default
# (1/2), strip-input/escape-output (2/3), optional-param/required-param (3/4).
BOUNDARY_FAMILIES = (
    Family(
        "comment-typo",
        "trivial",
        "chore(totals): fix a repeated word in a comment",
        "Remove the repeated word 'the' from the comment in total() in src/totals.py.",
        "The duplicated word is a typo that makes the comment read incorrectly.",
        "Validation plan: inspect the edited comment in src/totals.py; no test is added "
        "for a comment.",
        "Only this comment is edited; other comments in src/totals.py have not been reviewed.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/totals.py",
                "def total(prices):\n    # Add up the the prices.\n    t = 0\n"
                "    for price in prices:\n        t += price\n    return t\n",
                "def total(prices):\n    # Add up the prices.\n    t = 0\n"
                "    for price in prices:\n        t += price\n    return t\n",
                "chore(totals): fix a repeated word in a comment",
            ),
        ),
    ),
    Family(
        "rename-local",
        "trivial",
        "chore(totals): rename a local variable in total",
        "Rename the local variable t to running_total in total() in src/totals.py.",
        "The one-letter name is hard to read; a descriptive name makes the accumulation "
        "easier to follow.",
        "Validation plan: compare total() results for sample inputs before and after the "
        "rename; no new test is added.",
        "Only the variable in total() is renamed; other names in the module are unchanged.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/totals.py",
                "def total(prices):\n    # Add up the prices.\n    t = 0\n"
                "    for price in prices:\n        t += price\n    return t\n",
                "def total(prices):\n    # Add up the prices.\n    running_total = 0\n"
                "    for price in prices:\n        running_total += price\n"
                "    return running_total\n",
                "chore(totals): rename a local variable in total",
            ),
        ),
    ),
    Family(
        "extract-constant",
        "trivial",
        "chore(pager): define a page size constant",
        "Define _PAGE_SIZE = 100 in src/pager.py, use it in page(), and add "
        "tests/test_pager.py asserting that a 200-item input returns 100 rows.",
        "A named constant documents what the number 100 means and gives one place to read it.",
        "Validation plan: run tests/test_pager.py, which expects 100 rows from a 200-item input.",
        "Only src/pager.py is edited; other uses of the page size in the repository are "
        "not searched.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/pager.py",
                "def page(rows):\n    return rows[:100]\n",
                "_PAGE_SIZE = 100\n\n\ndef page(rows):\n    return rows[:_PAGE_SIZE]\n",
                "chore(pager): define a page size constant",
            ),
            Edit(
                "tests/test_pager.py",
                "",
                "from src.pager import page\n\n\n"
                "def test_page_length():\n    assert len(page(list(range(200)))) == 100\n",
                "test(pager): assert the page length",
            ),
        ),
    ),
    Family(
        "change-default",
        "non_trivial",
        "chore(pager): set the page size to 50",
        "Change _PAGE_SIZE from 100 to 50 in src/pager.py and add tests/test_pager.py "
        "asserting that a 200-item input returns 50 rows.",
        "Pages of 100 rows are too large for the narrow display that renders them; "
        "50 rows fit on one screen.",
        "Validation plan: run tests/test_pager.py, which expects 50 rows from a 200-item input.",
        "Only src/pager.py is edited; other uses of the page size in the repository are "
        "not searched.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/pager.py",
                "_PAGE_SIZE = 100\n\n\ndef page(rows):\n    return rows[:_PAGE_SIZE]\n",
                "_PAGE_SIZE = 50\n\n\ndef page(rows):\n    return rows[:_PAGE_SIZE]\n",
                "chore(pager): set the page size to 50",
            ),
            Edit(
                "tests/test_pager.py",
                "",
                "from src.pager import page\n\n\n"
                "def test_page_length():\n    assert len(page(list(range(200)))) == 50\n",
                "test(pager): assert the page length",
            ),
        ),
    ),
    Family(
        "strip-input",
        "non_trivial",
        "chore(names): strip whitespace in clean",
        "Return name.strip() from clean() in src/names.py and add tests/test_names.py "
        "asserting that '  Ada ' becomes 'Ada'.",
        "Names copied from forms often carry stray surrounding whitespace that prevents exact matches.",
        "Validation plan: run tests/test_names.py for the padded-name case.",
        "Only leading and trailing whitespace is handled; internal whitespace and "
        "non-string input are not addressed.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/names.py",
                "def clean(name):\n    return name\n",
                "def clean(name):\n    return name.strip()\n",
                "chore(names): strip whitespace in clean",
            ),
            Edit(
                "tests/test_names.py",
                "",
                "from src.names import clean\n\n\n"
                "def test_clean():\n    assert clean('  Ada ') == 'Ada'\n",
                "test(names): cover padded names",
            ),
        ),
    ),
    Family(
        "escape-output",
        "non_trivial",
        "chore(render): escape cell text",
        "Return html.escape(text) from cell() in src/render.py and add tests/test_render.py "
        "asserting that '<b>' becomes '&lt;b&gt;'.",
        "Cell text is inserted into HTML tables, where unescaped angle brackets alter the markup.",
        "Validation plan: run tests/test_render.py for the angle-bracket case.",
        "Only cell() is changed; other renderers and already-escaped input are not addressed.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/render.py",
                "def cell(text):\n    return text\n",
                "import html\n\n\ndef cell(text):\n    return html.escape(text)\n",
                "chore(render): escape cell text",
            ),
            Edit(
                "tests/test_render.py",
                "",
                "from src.render import cell\n\n\n"
                "def test_cell():\n    assert cell('<b>') == '&lt;b&gt;'\n",
                "test(render): cover angle brackets",
            ),
        ),
    ),
    Family(
        "optional-param",
        "non_trivial",
        "chore(client): add a timeout argument to fetch",
        "Add a timeout parameter to fetch() in src/client.py, defaulting to 30, and pass it "
        "to _get(); add tests/test_client.py covering fetch('u') and fetch('u', 5).",
        "Callers need to choose a timeout for slow endpoints instead of always waiting 30 seconds.",
        "Validation plan: run tests/test_client.py, which expects ('u', 30) from fetch('u') "
        "and ('u', 5) from fetch('u', 5).",
        "Only fetch() is changed; the timeout value is not validated.",
        "",
        False,
        "",
        "",
        "",
        (
            Edit(
                "src/client.py",
                "def _get(url, timeout):\n    return (url, timeout)\n\n\n"
                "def fetch(url):\n    return _get(url, 30)\n",
                "def _get(url, timeout):\n    return (url, timeout)\n\n\n"
                "def fetch(url, timeout=30):\n    return _get(url, timeout)\n",
                "chore(client): add a timeout argument to fetch",
            ),
            Edit(
                "tests/test_client.py",
                "",
                "from src.client import fetch\n\n\n"
                "def test_fetch():\n    assert fetch('u') == ('u', 30)\n"
                "    assert fetch('u', 5) == ('u', 5)\n",
                "test(client): cover both fetch call forms",
            ),
        ),
    ),
    Family(
        "required-param",
        "non_trivial",
        "chore(client): add a timeout argument to fetch",
        "Add a timeout parameter to fetch() in src/client.py, pass it to _get(), and pass 30 "
        "from the call in src/jobs.py.",
        "Callers need to choose a timeout for slow endpoints instead of always waiting 30 seconds.",
        "Validation plan: call run('u') in src/jobs.py and check that it returns ('u', 30).",
        "Only fetch() and its call in src/jobs.py are changed; the timeout value is not validated.",
        "",
        True,
        "",
        "",
        "",
        (
            Edit(
                "src/client.py",
                "def _get(url, timeout):\n    return (url, timeout)\n\n\n"
                "def fetch(url):\n    return _get(url, 30)\n",
                "def _get(url, timeout):\n    return (url, timeout)\n\n\n"
                "def fetch(url, timeout):\n    return _get(url, timeout)\n",
                "chore(client): add a timeout argument to fetch",
            ),
            Edit(
                "src/jobs.py",
                "from src.client import fetch\n\n\ndef run(url):\n    return fetch(url)\n",
                "from src.client import fetch\n\n\ndef run(url):\n    return fetch(url, 30)\n",
                "chore(jobs): pass an explicit timeout to fetch",
            ),
        ),
    ),
)

# Matched contrasts on the existing families (what-vs-why, concrete-but-partial coverage) and
# the boundary families are appended after the 100 base cases.
PROFILES = (
    "good",
    "nonconventional_title",
    "gibberish_title",
    "missing_rationale",
    "missing_change_description",
    "title_description_mismatch",
    "commit_description_mismatch",
    "diff_description_mismatch",
    "additional_cases",
    "combined_failures",
)

TRIVIALITY_LEVELS = [
    {
        "score": 0,
        "label": "editorial",
        "description": "Prose or link correction with no runtime or interface change.",
    },
    {
        "score": 1,
        "label": "mechanical",
        "description": "Localized mechanical or presentation edit in code; functional behavior and public interfaces are preserved.",
    },
    {
        "score": 2,
        "label": "bounded_behavior",
        "description": "Localized behavior or configuration change requiring edge-case review.",
    },
    {
        "score": 3,
        "label": "substantive",
        "description": "New capability or security-sensitive behavior requiring broader review.",
    },
    {
        "score": 4,
        "label": "incompatible",
        "description": "Removal of, or incompatible change to, a supported public interface or "
        "configuration requiring migration.",
    },
]

TRIVIALITY_ASSESSMENTS = {
    "guide-typo": (
        0,
        "docs/usage.md removes the repeated word 'the' from one sentence. "
        "The complete diff changes no executable code, configuration, or interface.",
    ),
    "cli-help": (
        1,
        "cli/help.js changes the exported help string from --ouput to --output. "
        "This changes displayed guidance, not option parsing; no parser or interface "
        "definition changes appear in the supplied patch.",
    ),
    "readme-link": (
        0,
        "README.md changes docs/geting-started.md to docs/getting-started.md. "
        "This is a documentation navigation correction with no runtime or interface change; "
        "the destination file's existence is not verified by this diff.",
    ),
    "empty-average": (
        2,
        "src/stats.py adds an empty-input guard returning 0 instead of reaching division "
        "by zero. The added test covers empty and ordinary inputs. This is a bounded "
        "behavior change requiring review of the new edge-case contract, not a mechanical edit.",
    ),
    "cache-key": (
        2,
        "src/cache.py replaces store.get(key) with store.get(key.lower()); tests exercise "
        "uppercase and lowercase lookups into a lowercase-keyed store. Lookup semantics "
        "and accepted key types can change, including mixed-case stored keys and keys "
        "without lower(). These are bounded behavior risks, not evidence of correctness.",
    ),
    "batch-limit": (
        2,
        "src/batch.py raises MAX_BATCH_SIZE from 50 to 100 while retaining the <= check; "
        "the added tests exercise 100 and 101 items. Previously rejected lengths 51–100 "
        "become accepted. This is a bounded validation-boundary change; downstream "
        "resource effects are not shown in the diff.",
    ),
    "token-compare": (
        3,
        "src/auth.py replaces == with hmac.compare_digest in valid_token and adds "
        "matching/mismatching string tests. Security-sensitive comparison behavior "
        "and supported input types require review beyond those two tests. The small "
        "diff does not establish timing safety or authentication correctness.",
    ),
    "csv-export": (
        3,
        "The added src/export.py introduces csv_text using StringIO and csv.writer "
        "with an explicit newline terminator. Its new test covers a comma-containing "
        "field and empty input. This is a new serialization capability requiring "
        "format, quoting, and input-contract review, even though it reuses the standard library.",
    ),
    "api-field": (
        4,
        "src/users.py removes the name response key and emits full_name instead; "
        "docs/api.md changes its public response example to match. Consumers expecting "
        "name lose that field without a compatibility alias in the patch. This is an "
        "incompatible documented response-shape change, regardless of the PR's claims.",
    ),
    "legacy-config": (
        4,
        "config/legacy.toml is deleted, removing its timeout_seconds and retries "
        "settings; docs/setup.md replaces the older-deployment instructions with "
        "config/default.toml for all deployments. The documented legacy entrypoint "
        "disappears, requiring migration for its users. The target configuration "
        "contents and actual deployment usage are not supplied.",
    ),
    "comment-typo": (
        0,
        "src/totals.py removes the repeated word 'the' from a comment. The diff shows no "
        "change to any executable line, so there is no runtime or interface effect.",
    ),
    "rename-local": (
        1,
        "src/totals.py renames the local variable t to running_total at its initialization, "
        "accumulation, and return. Every use in the function is updated and the signature is "
        "unchanged, so behavior and interfaces are preserved; this is a mechanical edit to "
        "code rather than prose.",
    ),
    "extract-constant": (
        1,
        "src/pager.py replaces the literal 100 with _PAGE_SIZE = 100 and slices with it; the "
        "added test asserts that a 200-item input still yields 100 rows. The value is "
        "unchanged and the new name is module-private, so functional behavior and the public "
        "interface are preserved.",
    ),
    "change-default": (
        2,
        "src/pager.py changes _PAGE_SIZE from 100 to 50, so page() returns at most 50 rows; "
        "the added test pins 50 rows for a 200-item input. Returned lengths change for inputs "
        "over 50 items, a bounded behavior change requiring edge-case review.",
    ),
    "strip-input": (
        2,
        "src/names.py changes clean from returning its input to returning name.strip(); the "
        "added test shows padded input becoming 'Ada'. Output changes for inputs with "
        "surrounding whitespace, and non-string inputs now raise AttributeError. This is a "
        "bounded local behavior change, not a new capability or security boundary.",
    ),
    "escape-output": (
        3,
        "src/render.py changes cell from returning text unchanged to html.escape(text); the "
        "added test shows '<b>' becoming '&lt;b&gt;'. HTML output encoding is "
        "security-sensitive and changes the rendered markup for every caller, including "
        "already-escaped input, so it needs broader review than a local normalization.",
    ),
    "optional-param": (
        3,
        "src/client.py adds a timeout parameter to fetch with default 30, and the added tests "
        "show fetch('u') still using 30 and fetch('u', 5) using 5. This adds a new public "
        "capability while remaining backward compatible; no existing call must change. "
        "Callers outside the patch are not shown.",
    ),
    "required-param": (
        4,
        "src/client.py makes timeout a required fetch parameter with no default, so existing "
        "fetch(url) calls fail with TypeError; src/jobs.py's one in-repo call is updated to "
        "pass 30. The supported call signature is removed, so callers outside the patch "
        "require migration even though the PR text does not say so. Other callers are not shown.",
    ),
}

# Both sets of text projections are authored independently from the stated claims.
TRUE_TEXT_TRIVIALITY = {
    "guide-typo": (0, "The stated work is a prose correction."),
    "cli-help": (
        1,
        "The stated work corrects help text while retaining the accepted parser option.",
    ),
    "readme-link": (0, "The stated work repairs a documentation link."),
    "empty-average": (2, "The stated work changes the result for empty input."),
    "cache-key": (2, "The stated work changes lookup normalization."),
    "batch-limit": (2, "The stated work changes the accepted batch-size boundary."),
    "token-compare": (
        3,
        "The stated work changes a comparison primitive to address timing security.",
    ),
    "csv-export": (
        3,
        "The stated work adds a new serialization helper with format and quoting behavior.",
    ),
    "api-field": (4, "The stated work renames a public response field."),
    "legacy-config": (4, "The stated work deletes an existing configuration entrypoint."),
    "comment-typo": (0, "The stated work corrects a repeated word in a comment."),
    "rename-local": (1, "The stated work renames a local variable."),
    "extract-constant": (
        1,
        "The stated work names a literal with an equal constant and asserts the same page length.",
    ),
    "change-default": (2, "The stated work changes a page-size value and the expected length."),
    "strip-input": (2, "The stated work adds whitespace stripping to one function."),
    "escape-output": (3, "The stated work adds HTML escaping to rendered cell text."),
    "optional-param": (
        3,
        "The stated work adds a timeout parameter with a default that preserves current calls.",
    ),
    "required-param": (
        3,
        "The stated work adds a parameter and one call-site value without stating that the "
        "parameter is required or that existing callers must change.",
    ),
}

FALSE_TEXT_TRIVIALITY = {
    "guide-typo": (0, "The claimed change only replaces written guidance with a video link."),
    "cli-help": (
        4,
        "The claim explicitly renames a parser option and removes the accepted old option.",
    ),
    "readme-link": (0, "The claimed change only replaces a documentation link target."),
    "empty-average": (2, "The claim changes empty-input behavior to a numeric result."),
    "cache-key": (2, "The claim changes key normalization and lookup semantics."),
    "batch-limit": (2, "The claim changes the accepted batch-size boundary."),
    "token-compare": (
        3,
        "The claim changes authentication-token matching, which remains security-sensitive "
        "even though it describes case normalization rather than a timing-security fix.",
    ),
    "csv-export": (
        3,
        "The claim introduces a new serialization capability, although its format is wrong.",
    ),
    "api-field": (1, "The claim explicitly presents formatting with unchanged response fields."),
    "legacy-config": (
        2,
        "The claim changes a timeout value while explicitly retaining both configuration paths.",
    ),
}

# (summary, testing) that concretely describe only the first edit of a multi-file family.
PARTIAL_COVERAGE = {
    "empty-average": (
        "Return 0 for an empty values list before division in src/stats.py.",
        "Validation plan: manually call average([]) and check that it returns 0.",
    ),
    "cache-key": (
        "Lowercase keys before cache lookup in src/cache.py.",
        "Validation plan: manually look up 'USER' in a lowercase-key store and confirm it resolves.",
    ),
    "batch-limit": (
        "Increase MAX_BATCH_SIZE from 50 to 100 in src/batch.py.",
        "Validation plan: manually check that a 100-item batch is accepted.",
    ),
    "token-compare": (
        "Use hmac.compare_digest instead of == in src/auth.py.",
        "Validation plan: manually compare equal and unequal ASCII tokens.",
    ),
    "csv-export": (
        "Add csv_text(rows) using csv.writer with LF line endings.",
        "Validation plan: write csv_text([['a', 'b']]) to a file and open it in a spreadsheet.",
    ),
    "api-field": (
        "Replace the JSON response key name with full_name in src/users.py.",
        "Validation plan: call user_response('Ada') and check the returned key.",
    ),
    "legacy-config": (
        "Delete config/legacy.toml.",
        "Validation plan: check that config/default.toml remains present.",
    ),
}

# Per-edit review comments (in edit order) that address each change and its implications, plus
# one open concern. The review describes the actual patches, whatever the PR text claims.
REVIEW_COMMENTS = {
    "guide-typo": (
        (
            "docs/usage.md: the repeated word is removed, so the sentence reads 'Read the "
            "guide before starting.' The edit is limited to prose; there is no runtime or "
            "interface effect to review.",
        ),
        "I did not check whether other pages in the guide repeat words in the same way.",
    ),
    "cli-help": (
        (
            "cli/help.js: the help string now says --output, matching the option the parser "
            "accepts. Only displayed text changes and parsing is not touched in this patch, "
            "so a user copying the example no longer gets an unknown-option error.",
        ),
        "Please confirm the parser registers the option as --output; this patch does not show it.",
    ),
    "readme-link": (
        (
            "README.md: the link target changes from docs/geting-started.md to "
            "docs/getting-started.md and the label is unchanged. No code or interface is "
            "touched.",
        ),
        "Please verify that docs/getting-started.md exists; the diff does not show it.",
    ),
    "empty-average": (
        (
            "src/stats.py: the new guard returns 0 for an empty list before the division, so "
            "[] no longer raises ZeroDivisionError. Callers that relied on that exception now "
            "receive 0, which is a behavior change worth noting.",
            "tests/test_stats.py: the new tests cover [] returning 0 and [2, 4] returning 3, "
            "pinning both the new edge case and the ordinary path.",
        ),
        "A returned 0 can be confused with a real average of zero; please confirm downstream "
        "reports accept that.",
    ),
    "cache-key": (
        (
            "src/cache.py: lookup now calls key.lower() before store.get, so mixed-case keys "
            "hit a lowercase store. Non-string keys will raise AttributeError, and a store "
            "holding distinct uppercase keys can no longer be reached through them.",
            "tests/test_cache.py: the test checks that 'USER' and 'user' both resolve to 7 in "
            "a lowercase-key store; it does not cover a store with uppercase keys.",
        ),
        "Please confirm writers always store lowercase keys, since only lookup is normalized.",
    ),
    "batch-limit": (
        (
            "src/batch.py: MAX_BATCH_SIZE rises from 50 to 100 and the <= comparison is "
            "unchanged, so lengths 51 to 100 that were rejected are now accepted. Peak memory "
            "per batch can grow accordingly.",
            "tests/test_batch.py: the boundary tests accept 100 items and reject 101, "
            "exercising the new limit exactly at its edge.",
        ),
        "No memory or throughput measurement accompanies the larger limit.",
    ),
    "token-compare": (
        (
            "src/auth.py: == is replaced by hmac.compare_digest, which avoids an early-exit "
            "comparison. It needs matching argument types and, for str, ASCII-only text; "
            "otherwise it raises TypeError, so callers passing non-ASCII strings or mixed "
            "bytes and str will now fail.",
            "tests/test_auth.py: matching and mismatching ASCII tokens are covered, but the "
            "tests cannot show timing behavior or non-ASCII inputs.",
        ),
        "Please confirm that no caller passes non-ASCII or bytes tokens.",
    ),
    "csv-export": (
        (
            "src/export.py: csv_text writes rows through csv.writer with LF line endings and "
            "returns the string, so fields containing commas are quoted. No header is added "
            "and formula escaping is not handled.",
            "tests/test_export.py: the tests cover a comma-containing field, expected to be "
            "quoted, and an empty list, expected to give an empty string.",
        ),
        "Spreadsheet formula injection (cells starting with =, +, - or @) is not mitigated; "
        "please decide whether that is in scope.",
    ),
    "api-field": (
        (
            "src/users.py: user_response returns full_name instead of name, so clients "
            "reading name lose the field. No alias or migration period is provided, which "
            "makes this a breaking API change.",
            "docs/api.md: the response example now shows full_name, matching the code.",
        ),
        "Please confirm that all known API consumers have been told before this merges.",
    ),
    "legacy-config": (
        (
            "config/legacy.toml: the file and its timeout_seconds and retries settings are "
            "deleted, so any deploy script that references it fails until migrated to "
            "config/default.toml.",
            "docs/setup.md: the legacy instructions are removed and config/default.toml is "
            "documented for all deployments.",
        ),
        "The patch does not show config/default.toml, so I cannot confirm it provides "
        "equivalent timeout and retry values.",
    ),
    "comment-typo": (
        (
            "src/totals.py: only the comment changes; the repeated word 'the' is removed. No "
            "executable line is edited, so total() behaves identically.",
        ),
        "Other comments in the module may have similar typos; I did not check.",
    ),
    "rename-local": (
        (
            "src/totals.py: the local variable t is renamed to running_total at its "
            "initialization, the accumulation, and the return. All three uses are updated and "
            "the signature of total() is unchanged, so every input gives the same result.",
        ),
        "The rename is local to total(); please confirm the longer name satisfies the "
        "project's line-length lint.",
    ),
    "extract-constant": (
        (
            "src/pager.py: the literal 100 becomes a module-private _PAGE_SIZE = 100 that "
            "page() slices with, so the page length is unchanged and the name is not part of "
            "the public interface.",
            "tests/test_pager.py: the new test asserts that a 200-item input yields 100 rows, "
            "confirming the length is the same as before.",
        ),
        "Please confirm that no other module duplicates the literal 100.",
    ),
    "change-default": (
        (
            "src/pager.py: _PAGE_SIZE changes from 100 to 50, so page() now returns at most "
            "50 rows instead of 100. Callers that expected 100-row pages receive fewer rows.",
            "tests/test_pager.py: the new test asserts that a 200-item input yields 50 rows, "
            "pinning the new page length.",
        ),
        "Please confirm downstream consumers cope with twice as many pages.",
    ),
    "strip-input": (
        (
            "src/names.py: clean now returns name.strip(), so leading and trailing whitespace "
            "is removed. Non-string inputs raise AttributeError, and names that differed only "
            "by surrounding whitespace now compare equal.",
            "tests/test_names.py: the test checks that '  Ada ' becomes 'Ada' but does not "
            "cover an already-clean name or an empty string.",
        ),
        "Please confirm that no caller passes None to clean.",
    ),
    "escape-output": (
        (
            "src/render.py: cell now returns html.escape(text), so <, >, & and quotes become "
            "entities before output. Rendered output changes for any text containing those "
            "characters, and the function becomes a security-relevant boundary against markup "
            "injection.",
            "tests/test_render.py: the test checks that '<b>' becomes '&lt;b&gt;' but does not "
            "cover already-escaped text, which would be escaped twice.",
        ),
        "Double escaping is possible if callers already escape their input.",
    ),
    "optional-param": (
        (
            "src/client.py: fetch gains a timeout parameter defaulting to 30 and passes it to "
            "_get, so existing fetch(url) calls keep the same 30-second timeout while callers "
            "can now choose another value.",
            "tests/test_client.py: the tests cover fetch('u') still using 30 and fetch('u', 5) "
            "using 5, but not a zero or negative timeout.",
        ),
        "Please decide whether invalid timeout values should be rejected.",
    ),
    "required-param": (
        (
            "src/client.py: fetch now requires a timeout argument with no default, so any "
            "existing fetch(url) call raises TypeError until it is updated. External callers "
            "will break.",
            "src/jobs.py: the one in-repo call is updated to pass 30, which preserves its "
            "previous timeout but does not help callers outside this patch.",
        ),
        "Please confirm whether a default or deprecation period is wanted before callers migrate.",
    ),
}

REVIEW_VARIANTS = ("none", "complete", "partial", "generic", "ambiguous", "open_concern")
REVIEW_OFF_TARGET_SOURCE = "empty-average"

CRITERIA = {
    "change_triviality": "Ordinal 0–4 assessment of the actual diff: higher scores mean "
    "less trivial changes; scores 0–1 are trivial.",
    "summary-section": "Description contains a populated level-two Summary section.",
    "review-coverage": "Supplied review addresses every substantive change; not_assessable when "
    "no review is supplied or its references cannot be tied to the patches. It describes the "
    "review, not the PR text, so it never contributes to expected_quality.",
    "title-format": "Rubric-ID mirror of title_format.",
    "change-rationale": "Rubric-ID mirror of rationale.",
    "description-coverage": "Rubric-ID mirror of description_diff_consistency.",
    "testing-explanation": "Rubric-ID mirror of testing.",
    "change-triviality": "Rubric-ID mirror of change_triviality.",
    "title_format": "Title follows the router's Conventional Commits syntax.",
    "title_meaningful": "Title conveys an intelligible change rather than gibberish.",
    "title_diff_consistency": "Meaningful title agrees with the actual diff.",
    "description_present": "Body contains substantive prose rather than only placeholders/headings.",
    "rationale": "Body explains a concrete reason for the actual changes, rather than restating them.",
    "changes_described": "Body concretely describes changes; factual accuracy is assessed separately.",
    "description_title_consistency": "Body's change claims agree with the title.",
    "description_commit_consistency": "Body's change claims agree with all commit messages.",
    "description_diff_consistency": "Body accurately covers substantive diff changes, including removals.",
    "limitations": "Body identifies relevant scope boundaries, risks, or limitations.",
    "breaking_changes": "Body accurately discloses and explains migration for an incompatible change.",
    "testing": "Body describes relevant validation or a specific reason testing does not apply.",
}
RUBRIC_LABEL_MIRRORS = {
    "title-format": "title_format",
    "change-rationale": "rationale",
    "description-coverage": "description_diff_consistency",
    "testing-explanation": "testing",
    "change-triviality": "change_triviality",
}


def digest(value: str) -> str:
    """Return a reproducible fictional SHA-shaped identifier.

    :param value: Deterministic identifier input, not a real Git object.
    :returns: Forty lowercase hexadecimal characters.
    """
    return hashlib.sha256(value.encode()).hexdigest()[:40]


def changed_file(edit: Edit) -> dict:
    """Build a complete unified patch with independently countable line metadata.

    :param edit: Original and replacement synthetic file contents.
    :returns: A router-compatible changed-file object.
    """
    status = "added" if not edit.before else "removed" if not edit.after else "modified"
    old_path = "/dev/null" if status == "added" else f"a/{edit.path}"
    new_path = "/dev/null" if status == "removed" else f"b/{edit.path}"
    lines = list(
        difflib.unified_diff(
            edit.before.splitlines(keepends=True),
            edit.after.splitlines(keepends=True),
            fromfile=old_path,
            tofile=new_path,
        )
    )
    return {
        "path": edit.path,
        "status": status,
        "additions": sum(line.startswith("+") for line in lines[2:]),
        "deletions": sum(line.startswith("-") for line in lines[2:]),
        "patch": f"diff --git a/{edit.path} b/{edit.path}\n" + "".join(lines),
        "patch_truncated": False,
    }


def review_for(
    family: Family, title: str, family_index: int, profile_index: int
) -> tuple[str, str, CriterionResult]:
    """Choose a deterministic review variant and its coverage label.

    The variant rotates with the family and profile positions so every family sees each variant.
    The review describes the real patches, whatever the PR text claims.

    :param family: Family whose patches the review discusses.
    :param title: Final PR title, repeated by the generic variant.
    :param family_index: Position of the family within its profile's families.
    :param profile_index: Position of the profile among all profiles.
    :returns: Variant name, review text (empty for ``none``), and review-coverage label.
    """
    variant = REVIEW_VARIANTS[(family_index - profile_index) % len(REVIEW_VARIANTS)]
    if variant == "partial" and len(family.edits) == 1:
        variant = "off_target"
    comments, concern = REVIEW_COMMENTS[family.identifier]
    covered = "The review individually addresses every changed file and its implications."
    if variant == "none":
        return (
            variant,
            "",
            {
                "status": "not_assessable",
                "explanation": "No review text is supplied for this case.",
                "sources": [],
            },
        )
    if variant == "complete":
        text = "\n".join(f"- {comment}" for comment in comments)
        status, explanation = "passed", covered
    elif variant == "open_concern":
        text = "\n".join(f"- {comment}" for comment in comments)
        text += f"\n\nOpen concern: {concern}\nThis needs an answer before merging."
        status = "passed"
        explanation = covered + " Its open concern does not reduce coverage."
    elif variant == "partial":
        text = f"- {comments[0]}\n\nOverall this looks fine."
        status = "failed"
        explanation = "The review addresses only the first changed file and omits the second."
    elif variant == "off_target":
        other = REVIEW_COMMENTS[REVIEW_OFF_TARGET_SOURCE][0]
        text = "\n".join(f"- {comment}" for comment in other)
        status = "failed"
        explanation = "The review discusses files and behavior that are not in this patch."
    elif variant == "generic":
        text = f"Reviewed: {title}\nLGTM, no further comments."
        status = "failed"
        explanation = (
            "The review is a generic approval that repeats the title and addresses no "
            "specific change."
        )
    else:
        text = (
            "Following up on the thread: the concern raised earlier about the second change "
            "is resolved by the latest push, and the boundary discussed offline behaves as "
            "agreed. Nothing further from me."
        )
        status = "not_assessable"
        explanation = (
            "The review refers only to unseen discussion and unspecified items, so it "
            "cannot be tied to specific changes in the patches."
        )
    return (
        variant,
        text + "\n",
        {
            "status": status,
            "explanation": explanation,
            "sources": ["review.md", "evidence.json#/files"],
        },
    )


def text_triviality_diagnostic(
    diff_score: int,
    title_score: int | None,
    body_score: int | None,
    title_explanation: str,
    body_explanation: str,
) -> dict:
    """Compare independently annotated text signals with the diff without scoring a criterion.

    :param diff_score: Existing diff-based reference score.
    :param title_score: Score supported by title wording, or no interpretable claim.
    :param body_score: Score supported by body wording, or insufficient change information.
    :param title_explanation: Evidence-based reason for the title projection.
    :param body_explanation: Evidence-based reason for the body projection.
    :returns: Unscored confounder metadata retaining conflicts and unknown signals.
    """
    signals = [value for value in (title_score, body_score) if value is not None]
    if not signals:
        text_score, assessment = None, "not_assessable"
        relationship = "not_assessable"
    elif len(set(signals)) > 1:
        text_score, assessment = None, "conflicting"
        relationship = "conflicting_text"
    else:
        text_score = signals[0]
        assessment = (
            "agreed"
            if len(signals) == 2
            else "title_only"
            if title_score is not None
            else "body_only"
        )
        relationship = (
            "text_understates"
            if text_score < diff_score
            else "text_overstates"
            if text_score > diff_score
            else "aligned"
        )
    return {
        "diff_score": diff_score,
        "title_score": title_score,
        "body_score": body_score,
        "text_score": text_score,
        "assessment": assessment,
        "relationship": relationship,
        "score_delta": None if text_score is None else text_score - diff_score,
        "potential_confounder": any(value != diff_score for value in signals) if signals else None,
        "title_explanation": title_explanation,
        "body_explanation": body_explanation,
        "sources": ["evidence.json#/title", "evidence.json#/body", "evidence.json#/files"],
    }


def make_case(
    number: int, family: Family, profile: str, family_index: int, profile_index: int = 0
) -> tuple[dict, ...]:
    """Construct one case and explicit criterion labels for its planted issues.

    :param number: Unique synthetic PR number.
    :param family: Factual change and truthful documentation.
    :param profile: Controlled quality scenario.
    :param family_index: Position used to choose additional and combined scenarios.
    :param profile_index: Position of the profile, used to rotate review variants.
    :returns: Evidence, commit metadata, and independent benchmark annotations.
    """
    case_id = f"pr-{number:03d}"
    title = family.title
    sections = {
        "Summary": family.summary,
        "Rationale": family.rationale,
        "Testing": family.testing,
        "Limitations": family.limitations,
        "Breaking changes": family.breaking,
    }
    messages = [edit.message for edit in family.edits]
    criteria: dict[str, CriterionResult] = {
        key: {"status": "passed", "explanation": explanation}
        for key, explanation in {
            "title_format": "The title uses a lowercase type, optional scope/!, and ': '.",
            "summary-section": "The body includes a populated level-two Summary section.",
            "review-coverage": "No supplied review text is available in this corpus.",
            "title_meaningful": "The title names a concrete, intelligible change.",
            "title_diff_consistency": "The title agrees with the reference change and patch.",
            "description_present": "The description contains substantive change information.",
            "rationale": "The rationale explains the concrete problem motivating the reference change.",
            "changes_described": "The description states concrete implementation changes.",
            "description_title_consistency": "The description and meaningful title describe the same change.",
            "description_commit_consistency": "The description covers the changes named by every commit.",
            "description_diff_consistency": "The summary covers all substantive changes in the patches.",
            "limitations": "The description names relevant scope limits or risks.",
            "breaking_changes": "The description discloses the incompatible change and required migration.",
            "testing": "The description gives a relevant validation plan, without claiming execution.",
        }.items()
    }
    if not family.incompatible:
        criteria["breaking_changes"] = {
            "status": "not_applicable",
            "explanation": "No public field or configuration entrypoint is removed in this family; "
            "any behavior changes and scope risks are still documented in the reference text.",
        }
    tags = [profile, family.complexity]
    if family.incompatible:
        tags.append("breaking_change")
    variant = profile
    body_override = None
    diff_score = TRIVIALITY_ASSESSMENTS[family.identifier][0]
    title_score, title_projection_reason = TRUE_TEXT_TRIVIALITY[family.identifier]
    body_score, body_projection_reason = TRUE_TEXT_TRIVIALITY[family.identifier]

    def mark(key: str, status: str, reason: str) -> None:
        """Record an explicit oracle result for one manipulated criterion.

        :param key: Criterion identifier.
        :param status: Expected status independent of any model output.
        :param reason: Explanation of the planted issue.
        """
        criteria[key] = {"status": status, "explanation": reason}

    def nonconventional() -> None:
        """Replace the title with meaningful text that lacks Conventional Commits syntax."""
        nonlocal title
        title = family.title.split(": ", 1)[1].capitalize()
        mark(
            "title_format",
            "failed",
            "The meaningful title lacks a conventional type and colon prefix.",
        )
        tags.append("nonconventional_title")

    def gibberish() -> None:
        """Use unintelligible titles, including examples with formally valid syntax."""
        nonlocal title, title_score, title_projection_reason
        title_score = None
        title_projection_reason = "The gibberish title provides no interpretable change claim."
        title = "asdf qwer zxcv 123" if family_index < 5 else "fix(core): blorb zzz flarp"
        if family_index < 5:
            mark(
                "title_format",
                "failed",
                "The title has neither a conventional prefix nor a meaningful subject.",
            )
        mark(
            "title_meaningful",
            "failed",
            "The title is gibberish and conveys no recognizable change.",
        )
        for key in ("title_diff_consistency", "description_title_consistency"):
            mark(
                key,
                "not_assessable",
                "An unintelligible title supplies no change claim to compare.",
            )
        tags.extend(
            [
                "gibberish_title",
                "valid_syntax_gibberish" if family_index >= 5 else "invalid_syntax_gibberish",
            ]
        )

    def missing_rationale(vague: bool = False) -> None:
        """Remove meaningful motivation while leaving the concrete summary intact.

        :param vague: Whether to substitute generic prose rather than omit the section.
        """
        if vague:
            sections["Rationale"] = "This makes things better and improves the project."
        else:
            sections.pop("Rationale", None)
        mark(
            "rationale",
            "failed",
            "The body offers only generic motivation."
            if vague
            else "The body describes what changed but never explains why it is needed.",
        )
        tags.append("vague_rationale" if vague else "missing_rationale")

    def missing_changes() -> None:
        """Keep motivation but remove concrete implementation and validation details."""
        nonlocal body_score, body_projection_reason
        body_score = None
        body_projection_reason = "The body provides motivation and generic scope statements without describing actual edits."
        sections.pop("Summary", None)
        # Family compatibility text often states the behavior change, so it would answer the omission.
        sections.pop("Breaking changes", None)
        sections["Testing"] = "Validation has not been performed; test selection is pending."
        sections["Limitations"] = (
            "Unrelated features remain outside scope; risk assessment is pending."
        )
        mark(
            "changes_described",
            "failed",
            "The body explains the problem but omits the actual edits.",
        )
        mark("testing", "failed", "No relevant checks or validation plan are described.")
        mark(
            "limitations", "failed", "The body defers assessment instead of naming concrete risks."
        )
        for key in (
            "description_title_consistency",
            "description_commit_consistency",
            "description_diff_consistency",
        ):
            mark(key, "not_assessable", "There is no concrete implementation account to compare.")
        if family.incompatible:
            sections.pop("Breaking changes", None)
            sections["Limitations"] = (
                "Compatibility details and migration guidance will follow separately."
            )
            mark(
                "limitations", "failed", "Compatibility guidance is deferred rather than explained."
            )
            mark(
                "breaking_changes",
                "failed",
                "The removed public interface and migration are omitted.",
            )
        tags.append("missing_change_description")

    def wrong_title() -> None:
        """Use a meaningful conventional title for a completely different change."""
        nonlocal title, title_score, title_projection_reason
        title_score = 2
        title_projection_reason = "The title claims a runtime-version configuration change; no new capability or removed public interface is stated."
        title = "chore(deps): upgrade the runtime to version 22"
        mark(
            "title_diff_consistency",
            "failed",
            "The title claims a runtime upgrade absent from the patches.",
        )
        mark(
            "description_title_consistency",
            "failed",
            "The title promises a runtime upgrade; the body describes " + family.summary,
        )
        tags.append("title_description_mismatch")

    def wrong_commits() -> None:
        """Give the genuine file patches misleading, unrelated commit messages."""
        messages[0] = "perf(db): add an index to the orders table"
        mark(
            "description_commit_consistency",
            "failed",
            "The first commit claims a database index, "
            "but the description and its actual patch concern " + family.identifier + ".",
        )
        tags.append("commit_description_mismatch")

    def wrong_diff(align_text: bool = True) -> None:
        """Make the summary contradict the real patch, optionally aligning other text.

        :param align_text: Whether the title and commit subjects follow the false summary.
        """
        nonlocal title, title_score, body_score, title_projection_reason, body_projection_reason
        body_score, body_projection_reason = FALSE_TEXT_TRIVIALITY[family.identifier]
        if family.identifier == "cli-help":
            body_projection_reason += (
                " The specific option-removal claim determines the level despite the generic "
                "footer claiming preserved interfaces."
            )
        sections["Summary"] = family.false_summary
        # Avoid truthful supplementary sections accidentally correcting the false summary.
        sections["Rationale"] = "The change described above is needed for a new consumer workflow."
        sections["Testing"] = "Validation has not been performed; test selection is pending."
        sections["Limitations"] = "Remaining edge cases have not been investigated."
        sections["Breaking changes"] = "None; all existing public interfaces are preserved."
        if align_text:
            title = family.false_title
            title_score, title_projection_reason = FALSE_TEXT_TRIVIALITY[family.identifier]
            messages[:] = [family.false_title] + [
                "chore: support the behavior described in the PR summary" for _ in messages[1:]
            ]
        else:
            if family.identifier == "empty-average":
                title = "fix(stats): return zero for an empty average input"
            mark(
                "description_title_consistency",
                "failed",
                "The false summary contradicts the title's stated result.",
            )
            mark(
                "description_commit_consistency",
                "failed",
                "The false summary contradicts the genuine commit subjects.",
            )
        mark(
            "title_diff_consistency",
            "failed" if align_text else "passed",
            "The title follows the false summary rather than the patch."
            if align_text
            else "The title still describes the genuine patch.",
        )
        mark(
            "description_diff_consistency",
            "failed",
            "The body claims: " + family.false_summary + " The actual change is: " + family.summary,
        )
        mark(
            "rationale",
            "failed",
            "The generic consumer-workflow claim does not explain the actual changes.",
        )
        mark(
            "limitations",
            "failed",
            "Uninvestigated edge cases are acknowledged without explaining the actual scope risks.",
        )
        mark("testing", "failed", "No relevant checks or validation plan are described.")
        if family.incompatible:
            mark(
                "breaking_changes",
                "failed",
                "The body claims compatibility while the patch removes a public interface.",
            )
        tags.append("diff_description_mismatch")

    def empty_body(kind: str) -> None:
        """Replace prose with empty, whitespace-only, or placeholder descriptions.

        :param kind: Empty-description variant.
        """
        nonlocal body_override, body_score, body_projection_reason
        body_score = None
        body_projection_reason = (
            "The empty or placeholder-only body supplies no substantive change claim."
        )
        body_override = {
            "empty_description": "",
            "whitespace_description": " \n\t\n",
            "placeholder_description": "TODO",
            "empty_headings": "## Summary\n\n## Testing\n",
        }[kind]
        for key in (
            "description_present",
            "rationale",
            "changes_described",
            "limitations",
            "testing",
        ):
            mark(key, "failed", "The description is empty or contains only a placeholder/headings.")
        for key in (
            "description_title_consistency",
            "description_commit_consistency",
            "description_diff_consistency",
        ):
            mark(key, "not_assessable", "No substantive description claims exist to compare.")
        if family.incompatible:
            mark(
                "breaking_changes",
                "failed",
                "No migration guidance accompanies the incompatible patch.",
            )
        tags.append(kind)

    def omit_section(section: str, criterion: str) -> None:
        """Remove one quality section and mark its corresponding omission.

        :param section: Markdown section to remove.
        :param criterion: Criterion whose content is missing.
        """
        sections.pop(section, None)
        mark(criterion, "failed", f"The description omits {section.lower()} information.")
        tags.append("missing_" + criterion)

    def partial_description() -> None:
        """Describe a serializer while omitting its independently added regression tests."""
        sections["Summary"] = family.partial_summary
        sections["Testing"] = (
            "Validation plan: write csv_text([['a', 'b']]) to a file and open it in a spreadsheet."
        )
        mark(
            "description_diff_consistency",
            "failed",
            "The helper is described, but the added "
            "regression test file and its quoting/empty-input coverage are omitted.",
        )
        mark(
            "description_commit_consistency",
            "failed",
            "The summary does not cover the separate test commit for quoting and empty input.",
        )
        tags.append("partial_description")

    def undocumented_break() -> None:
        """Omit both the compatibility disclosure and the migration information."""
        sections.pop("Breaking changes", None)
        sections["Limitations"] = "No additional performance measurements have been taken."
        mark(
            "breaking_changes",
            "failed",
            "The public removal is described but its "
            "incompatibility and required migration are not acknowledged.",
        )
        mark(
            "limitations",
            "failed",
            "The migration risk and lack of compatibility support are omitted.",
        )
        tags.append("undocumented_breaking_change")

    if profile == "nonconventional_title":
        nonconventional()
    elif profile == "gibberish_title":
        gibberish()
    elif profile == "missing_rationale":
        missing_rationale()
    elif profile == "missing_change_description":
        missing_changes()
    elif profile == "title_description_mismatch":
        wrong_title()
    elif profile == "commit_description_mismatch":
        wrong_commits()
    elif profile == "diff_description_mismatch":
        wrong_diff()
    elif profile == "additional_cases":
        variants = [
            "empty_description",
            "whitespace_description",
            "placeholder_description",
            "empty_headings",
            "vague_rationale",
            "missing_limitations",
            "missing_testing",
            "partial_description",
            "undocumented_breaking_change",
            "undocumented_breaking_change",
        ]
        variant = variants[family_index]
        if family_index < 4:
            empty_body(variant)
        elif family_index == 4:
            missing_rationale(vague=True)
        elif family_index == 5:
            omit_section("Limitations", "limitations")
        elif family_index == 6:
            omit_section("Testing", "testing")
        elif family_index == 7:
            partial_description()
        else:
            undocumented_break()
    elif profile == "combined_failures":
        if family_index == 0:
            nonconventional()
            missing_rationale()
        elif family_index == 1:
            gibberish()
            empty_body("empty_description")
        elif family_index == 2:
            wrong_title()
            missing_rationale()
        elif family_index == 3:
            wrong_diff(align_text=False)
        elif family_index == 4:
            nonconventional()
            missing_changes()
        elif family_index == 5:
            missing_rationale()
            omit_section("Limitations", "limitations")
        elif family_index == 6:
            wrong_commits()
            missing_rationale()
        elif family_index == 7:
            gibberish()
            partial_description()
        elif family_index == 8:
            nonconventional()
            missing_rationale()
            undocumented_break()
        else:
            wrong_commits()
            undocumented_break()
    elif profile == "what_not_why":
        # Unlike vague or missing rationale, the section is present and concrete but only restates.
        sections["Rationale"] = "This PR does the following: " + family.summary
        mark(
            "rationale",
            "failed",
            "The rationale restates the edits instead of explaining why they are needed.",
        )
        tags.append("what_not_why")
    elif profile == "omitted_change":
        sections["Summary"], sections["Testing"] = PARTIAL_COVERAGE[family.identifier]
        mark(
            "description_diff_consistency",
            "failed",
            "The summary concretely describes the first changed file and omits the second.",
        )
        mark(
            "description_commit_consistency",
            "failed",
            "The description does not cover the commit for the second changed file.",
        )
        tags.append("omitted_change")
    elif profile == "boundary":
        # Compatibility is deliberately not characterized, so the level must come from the diff.
        sections.pop("Breaking changes")
        if family.incompatible:
            mark(
                "breaking_changes",
                "failed",
                "The description names the new parameter but not that existing callers must migrate.",
            )
            tags.append("undocumented_breaking_change")
        tags.append("boundary")

    body = (
        body_override
        if body_override is not None
        else "\n\n".join(f"## {heading}\n{text}" for heading, text in sections.items()) + "\n"
    )
    files = [changed_file(edit) for edit in family.edits]
    created = datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(days=number - 1)
    author = {
        "login": f"synthetic-author-{family_index + 1:02d}",
        "name": f"Synthetic Author {family_index + 1:02d}",
        "email": f"author-{family_index + 1:02d}@example.invalid",
    }
    base_sha = digest(f"fictional-base:{family.identifier}")
    parent = base_sha
    commits = []
    for position, (message, file) in enumerate(zip(messages, files, strict=True)):
        sha = digest(case_id + parent + message + file["patch"])
        commits.append(
            {
                "sha": sha,
                "parents": [parent],
                "message": message,
                "author": author,
                "authored_at": (created - timedelta(minutes=10 - position)).isoformat(),
                "files": [file["path"]],
                "additions": file["additions"],
                "deletions": file["deletions"],
            }
        )
        parent = sha
    evidence = {
        "repository": "synthetic/pr-quality-demo",
        "number": number,
        "base_sha": base_sha,
        "head_sha": parent,
        "title": title,
        "body": body,
        "files_complete": True,
        "files": files,
    }
    summary_check = assess_pr_text(
        PullRequestEvidence.model_validate(evidence),
        Policy(title_format="any", required_body_sections=["Summary"]),
    )
    criteria["summary-section"] = {
        "status": "passed" if summary_check.valid else "failed",
        "explanation": (
            "The body includes a populated level-two Summary section."
            if summary_check.valid
            else summary_check.issues[0]
        ),
    }
    review_variant, _, review_label = review_for(family, title, family_index, profile_index)
    criteria["review-coverage"] = review_label
    metadata = {
        "schema_version": 1,
        "synthetic": True,
        "case_id": case_id,
        "url": f"https://example.invalid/synthetic/pr-quality-demo/pull/{number}",
        "state": "open",
        "draft": False,
        "author": author,
        "created_at": created.isoformat(),
        "updated_at": (created + timedelta(hours=1)).isoformat(),
        "base_ref": "main",
        "head_ref": f"synthetic/{case_id}",
        "base_sha": base_sha,
        "head_sha": parent,
        "changed_files": len(files),
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
        "commits": commits,
    }
    score, explanation = TRIVIALITY_ASSESSMENTS[family.identifier]
    criteria["change_triviality"] = {
        "type": "ordinal",
        "score": score,
        "level": TRIVIALITY_LEVELS[score]["label"],
        "is_trivial": score <= 1,
        "explanation": explanation,
    }
    for mirror, canonical in RUBRIC_LABEL_MIRRORS.items():
        criteria[mirror] = criteria[canonical].copy()
    source_map = {
        "change_triviality": ["evidence.json#/files"],
        "summary-section": ["evidence.json#/body"],
        "review-coverage": [],
        "title-format": ["evidence.json#/title"],
        "change-rationale": ["evidence.json#/body"],
        "description-coverage": ["evidence.json#/body", "evidence.json#/files"],
        "testing-explanation": ["evidence.json#/body", "evidence.json#/files"],
        "change-triviality": ["evidence.json#/files"],
        "title_format": ["evidence.json#/title"],
        "title_meaningful": ["evidence.json#/title"],
        "title_diff_consistency": ["evidence.json#/title", "evidence.json#/files"],
        "description_present": ["evidence.json#/body"],
        "rationale": ["evidence.json#/body"],
        "changes_described": ["evidence.json#/body"],
        "description_title_consistency": ["evidence.json#/body", "evidence.json#/title"],
        "description_commit_consistency": ["evidence.json#/body", "metadata.json#/commits"],
        "description_diff_consistency": ["evidence.json#/body", "evidence.json#/files"],
        "limitations": ["evidence.json#/body", "evidence.json#/files"],
        "breaking_changes": ["evidence.json#/body", "evidence.json#/files"],
        "testing": ["evidence.json#/body", "evidence.json#/files"],
    }
    for key, result in criteria.items():
        if key != "review-coverage":
            result["sources"] = source_map[key]
    annotation = {
        "schema_version": 3,
        "case_id": case_id,
        "label_status": "provisional",
        "label_origin": "synthetic_author_intent",
        "family_id": family.identifier,
        "scenario": profile,
        "variant": variant,
        "review_variant": review_variant,
        "complexity": family.complexity,
        "has_breaking_change": family.incompatible,
        "expected_quality": "needs_revision"
        if any(
            item.get("status") == "failed"
            for key, item in criteria.items()
            if key != "review-coverage"
        )
        else "good",
        "tags": sorted(set(tags)),
        "criteria": criteria,
        "diagnostics": {
            "text_diff_triviality": text_triviality_diagnostic(
                diff_score,
                title_score,
                body_score,
                title_projection_reason + f" Title: {title}",
                body_projection_reason
                + (
                    " Summary: " + sections["Summary"]
                    if body_override is None and "Summary" in sections
                    else ""
                ),
            ),
        },
        "reference_change": {
            "summary": family.summary,
            "rationale": family.rationale,
            "limitations": family.limitations,
            "breaking_changes": family.breaking,
        },
    }
    return evidence, metadata, annotation


ORACLE_STATUSES = {"passed": "passed", "failed": "failed", "not_assessable": "uncertain"}
RUBRIC_ROUTES = {"skip_and_approve": "skip_review", "suggest_changes": "review"}


def rubric_case(rubric: AssessmentRubric, annotation: dict) -> dict:
    """Derive router evaluation labels by applying the rubric's policy to oracle statuses.

    :param rubric: Assessment rubric whose criterion IDs are mirrored in the oracle.
    :param annotation: Oracle annotation for one case.
    :returns: Router evaluation case referencing the case's evidence.
    :raises KeyError: If an oracle status has no rubric equivalent.
    """
    oracle = annotation["criteria"]
    statuses = {}
    labels = {}
    for criterion in rubric.criteria:
        label = oracle[criterion.criterion_id]
        if label.get("type") == "ordinal":
            native = label["level"]
            statuses[criterion.criterion_id] = criterion.choice_statuses[native] or "uncertain"
        else:
            native = statuses[criterion.criterion_id] = ORACLE_STATUSES[label["status"]]
        if criterion.check == "semantic" and criterion.question_type == "choice":
            labels[criterion.criterion_id] = native
    advice = combine_assessments(
        rubric,
        [
            CriterionAssessment(
                criterion_id=criterion.criterion_id,
                status=statuses[criterion.criterion_id],
                source="deterministic",
                explanation="Oracle label.",
            )
            for criterion in rubric.criteria
        ],
        labels,
    )
    case_id = annotation["case_id"]
    case = {
        "case_id": case_id,
        "category": "rubric",
        "evidence": f"prs/{case_id}/evidence.json",
        "expectations": {
            "decision_routes": [RUBRIC_ROUTES.get(advice.recommendation, "needs_human_review")],
            "concerns": [],
            "rubric": {"recommendation": advice.recommendation, "criteria": statuses},
        },
    }
    if annotation["review_variant"] != "none":
        case["review"] = f"prs/{case_id}/review.md"
    return case


def write_json(path: Path, value: dict) -> None:
    """Write a stable, readable UTF-8 fixture.

    :param path: Output path under the corpus directory.
    :param value: JSON-serializable fixture.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def case_plan() -> list[tuple[str, int, int, Family]]:
    """List every case as profile, profile position, family position, and family.

    :returns: The 100 base cases followed by the contrastive and boundary extensions.
    """
    plan = [
        (profile, profile_index, family_index, family)
        for profile_index, profile in enumerate(PROFILES)
        for family_index, family in enumerate(FAMILIES)
    ]
    multi_file = [family for family in FAMILIES if len(family.edits) > 1]
    extensions = (
        ("what_not_why", FAMILIES),
        ("omitted_change", multi_file),
        ("boundary", BOUNDARY_FAMILIES),
    )
    for offset, (profile, families) in enumerate(extensions):
        plan += [
            (profile, len(PROFILES) + offset, family_index, family)
            for family_index, family in enumerate(families)
        ]
    return plan


def authored_case(spec: dict, author_id: str) -> tuple[dict, dict, dict, str | None]:
    """Package independently written source cases without applying scenario templates.

    :param spec: Complete authored text, file versions, and provisional reference judgments.
    :param author_id: Authoring agent provenance, separate from label-review provenance.
    :returns: CLI evidence, commit metadata, annotations, and optional supplied review.
    """
    case_id = spec["case_id"]
    number = int(case_id.removeprefix("pr-"))
    files = [changed_file(Edit(**edit)) for edit in spec["edits"]]
    author = {"login": author_id, "name": author_id, "email": f"{author_id}@example.invalid"}
    created = datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(days=number - 1)
    base_sha = parent = digest(f"fictional-base:{spec['family_id']}")
    commits = []
    for position, (edit, file) in enumerate(zip(spec["edits"], files, strict=True)):
        sha = digest(case_id + parent + edit["message"] + file["patch"])
        commits.append(
            {
                "sha": sha,
                "parents": [parent],
                "message": edit["message"],
                "author": author,
                "authored_at": (created - timedelta(minutes=10 - position)).isoformat(),
                "files": [file["path"]],
                "additions": file["additions"],
                "deletions": file["deletions"],
            }
        )
        parent = sha
    evidence = {
        "repository": "synthetic/pr-quality-demo",
        "number": number,
        "base_sha": base_sha,
        "head_sha": parent,
        "title": spec["title"],
        "body": spec["body"],
        "files_complete": True,
        "files": files,
    }
    metadata = {
        "schema_version": 1,
        "synthetic": True,
        "case_id": case_id,
        "url": f"https://example.invalid/synthetic/pr-quality-demo/pull/{number}",
        "state": "open",
        "draft": False,
        "author": author,
        "created_at": created.isoformat(),
        "updated_at": (created + timedelta(hours=1)).isoformat(),
        "base_ref": "main",
        "head_ref": f"synthetic/{case_id}",
        "base_sha": base_sha,
        "head_sha": parent,
        "changed_files": len(files),
        "additions": sum(file["additions"] for file in files),
        "deletions": sum(file["deletions"] for file in files),
        "commits": commits,
    }
    criteria = {key: dict(value) for key, value in spec["criteria"].items()}
    for key, value in criteria.items():
        value["sources"] = (
            (["review.md", "evidence.json#/files"] if spec["review"] else [])
            if key == "review-coverage"
            else (
                ["evidence.json#/title"]
                if key in {"title_format", "title_meaningful"}
                else ["evidence.json#/body", "metadata.json#/commits"]
                if key == "description_commit_consistency"
                else ["evidence.json#/body", "evidence.json#/files"]
            )
        )
    score = spec["triviality"]["score"]
    criteria["change_triviality"] = {
        "type": "ordinal",
        "score": score,
        "level": TRIVIALITY_LEVELS[score]["label"],
        "is_trivial": score <= 1,
        "explanation": spec["triviality"]["explanation"],
        "sources": ["evidence.json#/files"],
    }
    for mirror, canonical in RUBRIC_LABEL_MIRRORS.items():
        criteria[mirror] = criteria[canonical].copy()
    text = spec["text_signals"]
    annotation = {
        "schema_version": 3,
        "case_id": case_id,
        "label_status": "provisional",
        "label_origin": "synthetic_author_intent",
        "family_id": spec["family_id"],
        "scenario": spec["scenario"],
        "variant": spec["scenario"],
        "review_variant": spec["review_variant"],
        "complexity": "trivial" if score <= 1 else "non_trivial",
        "has_breaking_change": criteria["breaking_changes"]["status"] != "not_applicable",
        "expected_quality": "needs_revision"
        if any(
            item.get("status") == "failed"
            for key, item in criteria.items()
            if key != "review-coverage"
        )
        else "good",
        "tags": ["independently_authored", spec["scenario"]],
        "criteria": criteria,
        "diagnostics": {
            "text_diff_triviality": text_triviality_diagnostic(
                score,
                text["title_score"],
                text["body_score"],
                text["title_explanation"],
                text["body_explanation"],
            )
        },
        "reference_change": spec["reference_change"],
        "authorship": {"author_id": author_id, "method": "independent_agent_authored"},
        "contested_criteria": spec["contested"],
    }
    return evidence, metadata, annotation, spec["review"]


def main(output_root: Path = ROOT) -> None:
    """Regenerate the corpus, supplied reviews, and both indexes without network access.

    :param output_root: Directory in which to write the generated artifacts.
    """
    cases = []
    rubric = load_assessment_rubric(ROOT / "legacy-review/rubric.toml")
    router_cases = []
    packaged = []
    for number, (profile, profile_index, family_index, family) in enumerate(case_plan(), 1):
        evidence, metadata, annotation = make_case(
            number, family, profile, family_index, profile_index
        )
        _, review, _ = review_for(family, evidence["title"], family_index, profile_index)
        packaged.append((evidence, metadata, annotation, review))
    for source in sorted((ROOT / "authored").glob("author-*.json")):
        payload = json.loads(source.read_text(encoding="utf-8"))
        write_json(output_root / "authored" / source.name, payload)
        packaged.extend(authored_case(spec, payload["author_id"]) for spec in payload["cases"])
    for family_index, family in enumerate(BOUNDARY_FAMILIES):
        profile_index = len(PROFILES) + 4
        evidence, metadata, annotation = make_case(
            len(packaged) + 1, family, "boundary", family_index, profile_index
        )
        _, review, _ = review_for(family, evidence["title"], family_index, profile_index)
        packaged.append((evidence, metadata, annotation, review))
    for evidence, metadata, annotation, review in packaged:
        case_id = annotation["case_id"]
        directory = f"prs/{case_id}"
        for name, value in (
            ("evidence", evidence),
            ("metadata", metadata),
            ("annotations", annotation),
        ):
            write_json(output_root / directory / f"{name}.json", value)
        if review:
            (output_root / directory / "review.md").write_text(review, encoding="utf-8")
        else:
            (output_root / directory / "review.md").unlink(missing_ok=True)
        router_cases.append(rubric_case(rubric, annotation))
        cases.append(
            {
                "case_id": annotation["case_id"],
                "family_id": annotation["family_id"],
                "scenario": annotation["scenario"],
                "split": "evaluation" if "authorship" in annotation else "development",
                "author_id": annotation.get("authorship", {}).get(
                    "author_id", "template_generator"
                ),
                "variant": annotation["variant"],
                "complexity": annotation["complexity"],
                "triviality_score": annotation["criteria"]["change_triviality"]["score"],
                "triviality_level": annotation["criteria"]["change_triviality"]["level"],
                "is_trivial": annotation["criteria"]["change_triviality"]["is_trivial"],
                "text_triviality_score": annotation["diagnostics"]["text_diff_triviality"][
                    "text_score"
                ],
                "text_diff_triviality_relationship": annotation["diagnostics"][
                    "text_diff_triviality"
                ]["relationship"],
                "potential_triviality_confounder": annotation["diagnostics"][
                    "text_diff_triviality"
                ]["potential_confounder"],
                "title": evidence["title"],
                "expected_quality": annotation["expected_quality"],
                "tags": annotation["tags"],
                "failed_criteria": sorted(
                    key
                    for key, value in annotation["criteria"].items()
                    if value.get("status") == "failed"
                    and key not in RUBRIC_LABEL_MIRRORS
                    and key != "review-coverage"
                ),
                "review_variant": annotation["review_variant"],
                "review_coverage": annotation["criteria"]["review-coverage"]["status"],
                "review": directory + "/review.md" if review else None,
                "evidence": directory + "/evidence.json",
                "metadata": directory + "/metadata.json",
                "annotations": directory + "/annotations.json",
            }
        )
    write_json(
        output_root / "corpus.json",
        {
            "corpus_version": 1,
            "label_status": "provisional",
            "assessment_rubric": "legacy-review/rubric.toml",
            "cases": router_cases,
        },
    )
    partitions = {}
    for split in ("development", "evaluation"):
        selected = [case for case in cases if case["split"] == split]
        selected_ids = {case["case_id"] for case in selected}
        partitions[split] = {
            "family_ids": sorted({case["family_id"] for case in selected}),
            "case_ids": [case["case_id"] for case in selected],
        }
        write_json(
            output_root / f"corpus-{split}.json",
            {
                "corpus_version": 1,
                "label_status": "provisional",
                "assessment_rubric": "legacy-review/rubric.toml",
                "cases": [case for case in router_cases if case["case_id"] in selected_ids],
            },
        )
    write_json(
        output_root / "splits.json",
        {
            "schema_version": 1,
            "unit": "family_id",
            "protocol": "Original 125 cases plus eight matched boundary variants for development (133 cases); "
            "24 independently authored cases in twelve disjoint families held out for evaluation. "
            "Freeze this assignment before running models; evaluation is public and is not a sealed test set.",
            "partitions": partitions,
        },
    )
    write_json(
        output_root / "index.json",
        {
            "schema_version": 3,
            "synthetic": True,
            "label_status": "provisional",
            "case_count": len(cases),
            "criteria": CRITERIA,
            "rubric_label_mirrors": RUBRIC_LABEL_MIRRORS,
            "triviality_scale": {
                "minimum_score": 0,
                "maximum_score": 4,
                "direction": "higher_scores_are_less_trivial",
                "trivial_scores": [0, 1],
                "levels": TRIVIALITY_LEVELS,
            },
            "triviality_counts": dict(
                sorted(Counter(str(case["triviality_score"]) for case in cases).items())
            ),
            "complexity_counts": dict(Counter(case["complexity"] for case in cases)),
            "text_diff_triviality_counts": dict(
                Counter(case["text_diff_triviality_relationship"] for case in cases)
            ),
            "scenario_counts": dict(Counter(case["scenario"] for case in cases)),
            "quality_counts": dict(Counter(case["expected_quality"] for case in cases)),
            "review_variant_counts": dict(Counter(case["review_variant"] for case in cases)),
            "review_coverage_counts": dict(Counter(case["review_coverage"] for case in cases)),
            "cases": cases,
        },
    )
    lines = [
        "# Synthetic PR index",
        "",
        "Generated by `generate.py`. See [README.md](README.md) "
        "for the rubric and loading instructions. Each ID links to CLI-compatible evidence; "
        "the adjacent files contain commits and independent labels.",
        "",
        "Triviality: 0 editorial, 1 mechanical, 2 bounded behavior, "
        "3 substantive/security-sensitive, 4 incompatible. Scores 0–1 are trivial. "
        "Scores describe the actual diff independently of description quality.",
        "",
        "Text signals are diagnostic metadata, excluded from the criteria and scoring. "
        "Unknown or conflicting text receives no combined score. See annotations for separate title/body signals.",
        "",
        "Review: the variant of the supplied `review.md` and its review-coverage label. "
        "It describes the review, not the PR text, so it never changes Quality or Failed criteria.",
        "",
        "Development contains the original 125 cases plus eight matched boundary variants "
        "(133 cases, 18 families). Evaluation holds out "
        "24 independently authored cases (12 new families). See [splits.json](splits.json).",
        "",
        "| PR | Family | Triviality (0–4) | Trivial? | Text score / relationship | Scenario / variant | Quality | Failed criteria | Review | Companions | Partition / author |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        failures = ", ".join(case["failed_criteria"]) or "None"
        variant = case["scenario"] + (
            " / " + case["variant"] if case["variant"] != case["scenario"] else ""
        )
        review_link = (
            f"[{case['review_variant']}]({case['review']})"
            if case["review"]
            else case["review_variant"]
        )
        lines.append(
            f"| [{case['case_id']}]({case['evidence']}) | {case['family_id']} | "
            f"{case['triviality_score']} ({case['triviality_level']}) | "
            f"{'yes' if case['is_trivial'] else 'no'} | "
            f"{case['text_triviality_score'] if case['text_triviality_score'] is not None else 'unknown'} / "
            f"{case['text_diff_triviality_relationship']} | "
            f"{variant} | {case['expected_quality']} | {failures} | "
            f"{review_link} ({case['review_coverage']}) | "
            f"[commits]({case['metadata']}) / [labels]({case['annotations']}) | "
            f"{case['split']} / {case['author_id']} |"
        )
    (output_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(cases)} fictional PRs under {output_root}.")


if __name__ == "__main__":
    main()
