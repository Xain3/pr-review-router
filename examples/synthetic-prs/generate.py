"""Generate deterministic, fictional PR evidence and independent benchmark labels."""

import difflib
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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
        "description": "Supported public interface or configuration removal requiring migration.",
    },
]

TRIVIALITY_ASSESSMENTS = {
    "guide-typo": (0, "Only a repeated word in prose is removed; no runtime behavior changes."),
    "cli-help": (
        1,
        "Only the displayed option spelling changes in a help string; "
        "the option parser and accepted option are unchanged.",
    ),
    "readme-link": (0, "Only a misspelled relative documentation link is corrected."),
    "empty-average": (
        2,
        "Empty input changes from a division error to a numeric result; "
        "this needs edge-case review despite the small patch.",
    ),
    "cache-key": (
        2,
        "Lowercasing changes lookup semantics and can affect stores "
        "with differently capitalized keys.",
    ),
    "batch-limit": (
        2,
        "The accepted batch-size boundary changes from 50 to 100, "
        "affecting validation and potential memory use.",
    ),
    "token-compare": (
        3,
        "The comparison primitive changes in security-sensitive "
        "authentication logic; a short diff does not make this trivial.",
    ),
    "csv-export": (
        3,
        "A new serialization helper introduces quoting, empty-input, "
        "and format behavior beyond an editorial or mechanical edit.",
    ),
    "api-field": (
        4,
        "The supported response key name is removed and replaced "
        "with full_name; existing consumers must migrate.",
    ),
    "legacy-config": (
        4,
        "A supported configuration entrypoint is deleted; "
        "deployment scripts referencing it must migrate.",
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

CRITERIA = {
    "change_triviality": "Ordinal 0–4 assessment of the actual diff: higher scores mean "
    "less trivial changes; scores 0–1 are trivial.",
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


def make_case(number: int, family: Family, profile: str, family_index: int) -> tuple[dict, ...]:
    """Construct one case and explicit criterion labels for its planted issues.

    :param number: Unique synthetic PR number.
    :param family: Factual change and truthful documentation.
    :param profile: Controlled quality scenario.
    :param family_index: Position used to choose additional and combined scenarios.
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
    criteria = {
        key: {"status": "passed", "explanation": explanation}
        for key, explanation in {
            "title_format": "The title uses a lowercase type, optional scope/!, and ': '.",
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
        sections["Testing"] = "Validation plan: manually inspect the generated CSV."
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
    source_map = {
        "change_triviality": ["evidence.json#/files"],
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
        result["sources"] = source_map[key]
    annotation = {
        "schema_version": 2,
        "case_id": case_id,
        "label_status": "provisional",
        "label_origin": "synthetic_author_intent",
        "family_id": family.identifier,
        "scenario": profile,
        "variant": variant,
        "complexity": family.complexity,
        "has_breaking_change": family.incompatible,
        "expected_quality": "needs_revision"
        if any(item.get("status") == "failed" for item in criteria.values())
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


def write_json(path: Path, value: dict) -> None:
    """Write a stable, readable UTF-8 fixture.

    :param path: Output path under the corpus directory.
    :param value: JSON-serializable fixture.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(output_root: Path = ROOT) -> None:
    """Regenerate the 100-case corpus and both indexes without network access.

    :param output_root: Directory in which to write the generated artifacts.
    """
    cases = []
    for profile_index, profile in enumerate(PROFILES):
        for family_index, family in enumerate(FAMILIES):
            number = profile_index * len(FAMILIES) + family_index + 1
            evidence, metadata, annotation = make_case(number, family, profile, family_index)
            directory = f"prs/pr-{number:03d}"
            for name, value in (
                ("evidence", evidence),
                ("metadata", metadata),
                ("annotations", annotation),
            ):
                write_json(output_root / directory / f"{name}.json", value)
            cases.append(
                {
                    "case_id": annotation["case_id"],
                    "family_id": family.identifier,
                    "scenario": profile,
                    "variant": annotation["variant"],
                    "complexity": family.complexity,
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
                    ),
                    "evidence": directory + "/evidence.json",
                    "metadata": directory + "/metadata.json",
                    "annotations": directory + "/annotations.json",
                }
            )
    write_json(
        output_root / "index.json",
        {
            "schema_version": 2,
            "synthetic": True,
            "label_status": "provisional",
            "case_count": len(cases),
            "criteria": CRITERIA,
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
        "| PR | Family | Triviality (0–4) | Trivial? | Text score / relationship | Scenario / variant | Quality | Failed criteria | Companions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        failures = ", ".join(case["failed_criteria"]) or "None"
        variant = case["scenario"] + (
            " / " + case["variant"] if case["variant"] != case["scenario"] else ""
        )
        lines.append(
            f"| [{case['case_id']}]({case['evidence']}) | {case['family_id']} | "
            f"{case['triviality_score']} ({case['triviality_level']}) | "
            f"{'yes' if case['is_trivial'] else 'no'} | "
            f"{case['text_triviality_score'] if case['text_triviality_score'] is not None else 'unknown'} / "
            f"{case['text_diff_triviality_relationship']} | "
            f"{variant} | {case['expected_quality']} | {failures} | "
            f"[commits]({case['metadata']}) / [labels]({case['annotations']}) |"
        )
    (output_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(cases)} fictional PRs under {output_root}.")


if __name__ == "__main__":
    main()
