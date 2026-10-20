# Local models and offline replay

Mocks remain the CLI default. Explicit provider configuration selects Ollaya or a
structured-output Ollama LLM for decisions and optionally Ollama for legacy
code-defect assessment. The decision adapters, semantic rubric support, and
transport safeguards remain reusable for PR communication checks. The code-review
stage and defect-based metrics below are legacy experiments outside the intended
product scope; they do not implement the planned reporter, which consumes a
finalized assessment and cannot change the result. See the
[target architecture](../../docs/specification.md#intended-pr-communication-check).

These commands still use current decision/review interfaces. Reports remain
advisory, and no command fetches, posts to, approves, or merges a PR.

## Offline example

```sh
uv run pr-review-router evaluate \
  --corpus examples/local-models/corpus.json \
  --providers-config examples/local-models/replay.toml \
  --replay-dir examples/local-models/recordings \
  --output-dir reports/replay-evaluation
```

These tapes have `origin: "synthetic"`: invented HTTP responses exercise the
real adapters without claiming any live model review. The planted-defect tape
deliberately recommends an unsafe skip. Shadow mode still reviews the change,
and evaluation counts the unsafe recommendation. Incomplete evidence has an
empty tape because preflight prevents even metadata calls.

The five cases cover editorial, correct-code, planted-defect, ambiguous, and
incomplete evidence. Reference routes and concern anchors live in `corpus.json`,
separate from responses. Labels are `provisional`; a human should inspect them
before marking `label_status` as `human_reviewed` or tuning thresholds. Expand
this small corpus with representative sanitized cases before assessing quality.

## Live local evaluation

The review alias in this section executes legacy code assessment. It is not a
requirement or recommendation for implementing the intended reporter.

Check `ollaya list` and `ollama list`. This example uses installed `winnow:e4b`
and creates a review alias from `qwen3.5:2b`. Edit the model names and Modelfile
to match your inventory. The router never downloads models or starts servers.

```sh
ollama create pr-review-local -f examples/local-models/Modelfile

uv run pr-review-router review \
  --input examples/local-models/correct-code.json \
  --providers-config examples/local-models/providers.toml \
  --output reports/local-review.json \
  --experiment-output reports/local-experiment.json

uv run pr-review-router evaluate \
  --corpus examples/local-models/corpus.json \
  --providers-config examples/local-models/providers.toml \
  --output-dir reports/local-evaluation
```

Ollaya's `/v1/systemone` rejects context overflow instead of silently truncating
state. Small-context models may reject even small PRs; complete evidence,
including patches, is always sent. [Ollaya compatibility](https://ollaya.dev/docs/typesafe-compatibility).

Ollama's compatibility API cannot set context size per request. The adapter
requires explicit `num_ctx` from `/api/show` matching `context_tokens`. Input
is conservatively budgeted using UTF-8 request/schema/template bytes, framing
headroom, and reserved output tokens against configured and architectural
context limits. This assumes byte-fallback tokenization and ordinary templates;
inspect custom templates/tokenizers before adopting them. It can reject an
input that would fit after tokenization. [Ollama context configuration](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size).

Provider TOML is separate from policy and has no environment overrides. Each
role selects `backend = "mock"`, or its local backend with an explicit `model`
and loopback `endpoint` origin, without `/v1` or credentials. Defaults are a
120-second total HTTP deadline and 1 MiB response limit. Review defaults are
context 32768, standard/deep output 1024/2048, temperature 0, seed 0, and
`reasoning_effort = "none"`; select another supported effort for your model.
The Ollaya decision model's `max_choice_options` advertises how many labels it
can accept in a choice question, including semantic assessment-rubric questions;
it defaults to `3` and may be configured from `2` through `100` per model.
Optional `instructions` are model-specific; `backend_instructions` is available
for backend-specific supplements. Both are appended after the adapter's baseline
and cannot override its evidence-safety, output-contract, uncertainty, or
advisory-only requirements.
Both depths use the same model with distinct prompts and budgets, which does
not establish independent corroboration. Fixed settings do not guarantee
identical live generations.

`shadow_skips` must be true. Original decisions are recorded separately; skip
recommendations become review requests in the advisory report with an explicit
reason. Both direct-decision policies must be disabled. Existing `--config`
and policy environment overrides otherwise work as before. Ollaya native scores
are retained, but routing confidence is `unavailable` until task calibration
has identifiable provenance. Reviewer confidence is `self_reported`.

Loopback transport ignores system proxies, does not follow redirects, and
supports unauthenticated local servers only. Cloud/remote model selectors and
remote models advertised by Ollama are rejected. Local server metadata remains
a trust boundary. Authenticated local review transport remains later work;
hosted Jev decision support is documented in the [Jev examples](../jev/README.md).

## Explicit recording and replay

```sh
# Refresh tapes only when explicitly requested; use sanitized public evidence.
uv run pr-review-router evaluate \
  --corpus examples/local-models/corpus.json \
  --providers-config examples/local-models/providers.toml \
  --record --output-dir reports/recorded-evaluation

uv run pr-review-router evaluate \
  --corpus examples/local-models/corpus.json \
  --providers-config examples/local-models/providers.toml \
  --replay-dir reports/recorded-evaluation \
  --output-dir reports/replayed-evaluation
```

Single reviews support `--record PATH` or `--replay PATH`. Recording may replace
the chosen tape and includes full HTTP request/response bodies. Keep private
runs in ignored `reports/`. Manually inspect and sanitize recordings before
committing any to `examples/`; re-record sanitized evidence rather than editing
requests while retaining an old fingerprint. Recording and replay are mutually
exclusive and never refresh implicitly.

Replay matches provider/model settings, policy, evidence, prompt contents and
versions, and schemas through a fingerprint. Every HTTP request and its order
must match, including metadata and schema retries. Missing, extra, or changed
exchanges fail with status 2 without network fallback. Normal provider failures
produce advisory handoffs under default policy. HTTP/deadline failures are not
retried; invalid schemas receive the existing single retry. Raw server errors
never enter advisory reports.

Configured reviews always write a separate experiment artifact, defaulting to
`reports/experiment-PR_NUMBER-HEAD_HASH.json`. Evaluation writes one report and
artifact per case, optional `CASE_ID.tape.json` files, and `summary.json`. Files
are individually atomic and private on POSIX. A failed later case can leave
earlier outputs; summaries are written only after completion, so check exit
status before using an existing summary.

Artifacts retain model digests, server versions, generation settings, prompt
and schema revision, original decisions, uncapped review results, usage, and
latency. Metrics count schema/transport failures, unsafe skip recommendations,
missed planted defects, findings unsupported by reference anchors, and human
handoffs. Matching by path and optional line is a proxy; finding meaning needs
human assessment. Replay uses recorded latency and verifies integration, not
current model quality or speed.

Hosted adapters can later reuse the corpus, contracts, and replay mechanism,
with explicit credentials and provider capability tests. Task calibration and
honoring model skips follow human-reviewed evaluation.

## Catalog shortcuts

The top-level [models.toml](../../models.toml) includes `local` and `thorough`
profiles. After completing the local server/model setup above, run:

```sh
uv run pr-review-router review --input examples/local-models/editorial.json --profile local
uv run pr-review-router review --input examples/local-models/editorial.json --profile thorough --reviewer local --model-override 'review.deep_max_tokens=3072'
uv run pr-review-router evaluate --corpus examples/local-models/corpus.json --profile local --output-dir reports/catalog-evaluation
```

These commands perform live local inference. The catalog's default selection
remains offline mocks. Add named variants under `[models.NAME]`, then select
names with `--decider` / `--reviewer`, or add partial `[profiles.NAME]` selections.
Defaults are overridden by profiles, then explicit names, then setting overrides.
Keep using `--providers-config` for existing standalone files and synthetic tapes;
it bypasses the catalog and cannot be combined with catalog flags.

The catalog declares `text_generation` and `structured_output` capabilities per
live variant. System One entries cannot generate reviews. The current Ollama
reviewer requires structured output as well as text generation; declaring a
text-only variant does not provide a compatible reviewer adapter. Structured-output
LLMs support both roles through separate chat adapters. These declarations are
owner-supplied assumptions, not server discovery or proof of review quality.

## Structured LLM decisions

**Warning:** General LLM deciders are a non-optimal compatibility fallback.
Prefer a native System One decider (`ollaya` or `jev`) for the decision role.
Generated probabilities and confidence are self-reported, not native or
calibrated. Both `review` and `evaluate` print this warning once to stderr when
an LLM decider is selected, including through defaults, profiles, legacy files,
and replay. JSON output and exit status are unaffected. LLM reviewers alone
do not trigger the warning.

The `llm` catalog profile uses a local LLM for both roles. After the server/model
setup above, run:

```sh
uv run pr-review-router review --input examples/local-models/editorial.json --profile llm
uv run pr-review-router evaluate --corpus examples/rubric-review/corpus.json --decider local --reviewer local-high --model-override 'decision.standard_max_tokens=4096' --output-dir reports/llm-rubric
uv run pr-review-router review --input examples/local-models/editorial.json --providers-config examples/local-models/llm.toml
```

These are opt-in live commands, not offline replay. The standalone
[llm.toml](llm.toml) selects a real LLM decider and a demonstration mock reviewer.
The adapter translates typed choice, noul/predicate, and score questions into
strict JSON-schema chat completions. `standard_max_tokens` controls the output
budget for the whole decision batch; increase it for larger rubrics while
keeping complete context and output within the configured model context.

Generated probabilities, confidence, and scores are labeled `llm_self_reported`;
they are not native probabilities or task calibration. Task confidence stays
unavailable, skips stay shadowed, and incomplete/invalid responses escalate.
Using the same model for both roles does not supply independent model agreement.
Synthetic test recordings exercise both CLI commands without inference or
credentials; existing System One recordings remain usable.
