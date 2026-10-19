# Hosted Jev Decisions

This configuration explicitly sends complete PR evidence to TypeSafe over HTTPS
and can incur API charges. Obtain owner review of hosted data handling before
sending private evidence. Set `TYPESAFE_API_KEY` securely in your environment;
never put its value in TOML, commands committed to Git, or reports.

The example pins `jev-1.14.0`. Moving aliases are rejected. `allow_network = true`
is mandatory even for this separate provider configuration; loading no provider
configuration still selects offline mocks. Ollaya/Ollama remain local-only.

```sh
uv run pr-review-router review \
  --input examples/local-models/editorial.json \
  --providers-config examples/jev/providers.toml \
  --output reports/jev.report.json \
  --experiment-output reports/jev.experiment.json

uv run pr-review-router evaluate \
  --corpus examples/rubric-review/corpus.json \
  --providers-config examples/jev/providers.toml \
  --output-dir reports/jev-rubric
```

Jev performs route selection and semantic Choice/Noul/Score checklist assessment,
not substantive code review or free-form finding generation. The selected mock
reviewer is demonstration-only. Configure an independent local Ollama reviewer
as in [local model examples](../local-models/README.md), or hand substantive
review to a human. Keep provider roles distinct.

Choice and Score confidence describe their native distributions; Noul supplies
only a yes-probability. None is a calibrated probability that a PR is safe.
Task confidence stays unavailable, direct decisions stay disabled, and skip
recommendations are shadowed into review. Choice and Score rubric criteria may
set `uncertain_below_confidence`; thresholds remain experimental until evaluated
against human-reviewed PR labels. The supplied corpus labels are provisional.

## Budgets and Provenance

The SDK client is reused within one experiment. Configuration exposes I/O
timeouts, total retry scheduling budget, retry count, response bytes, Choice
options, and complete request/per-question byte budgets. `Retry-After` is honored
within retry limits. A question budget includes the full state, not just its
instructions. The conservative byte budgets are not exact token counts; context
errors fail closed without removing evidence. Long-context mode is not enabled.

Artifacts retain the resolved model, SDK version, request ID when supplied,
usage, native answers, and attempt counts. They belong under ignored `reports/`.
SDK wire-body logging is disabled in the router process, even when
`TYPESAFE_LOG_LEVEL` requests debug output. Authentication, transport, context,
missing/wrong answers, and exhausted retries cannot become a passing assessment.

## Recording and Replay

Recording requires a second explicit consent: change `allow_recording` to `true`
in an owner-controlled copy of the provider TOML, then add `--record
reports/jev.tape.json` to `review`, or `--record` to `evaluate`.
Only do this with sanitized evidence. Tapes exclude credentials and failure
bodies but retain full request state and successful answers. Artifacts can also
contain private review text; they are not a substitute for data-handling review.

Replay uses the same provider configuration and evidence, but needs no API key
and creates no SDK client:

```sh
uv run pr-review-router review \
  --input examples/local-models/editorial.json \
  --providers-config reports/jev-recording.toml \
  --replay reports/jev.tape.json \
  --output reports/jev-replay.report.json \
  --experiment-output reports/jev-replay.experiment.json
```

Here `reports/jev-recording.toml` means the unchanged owner-controlled
configuration used to make the recording. Fingerprints bind all settings,
evidence, schemas/prompts, and SDK version; changed inputs or unused/missing
exchanges reject replay without live fallback. Hosted tapes store one final
exchange per SDK call and its attempt count, not transient retry response bodies.
Credential-free client setup failures are recorded as zero-attempt configuration
exchanges. Schema-failure markers preserve the provider's single strict-schema
retry during replay.
No live recording or hosted quality claim is included in these examples.