# Direct decisions and non-blocking feedback

These offline fixtures add configurable routing alongside the existing skip,
standard/deep review, PR-text, rubric, and human-handoff paths. A decision
provider (decider) can directly reject or accept; a review provider (reviewer)
can return feedback that leaves the human free to act on it without requiring
a human step in the route.

```sh
uv run pr-review-router review --input examples/direct/reject.json --config examples/direct/policy.toml
uv run pr-review-router review --input examples/direct/feedback.json --config examples/direct/policy.toml
uv run pr-review-router review --input examples/direct/accept.json --config examples/direct/policy.toml
uv run pr-review-router review --input examples/direct/accept_feedback.json --config examples/direct/combined.toml
```

| Input / policy | Outcome | Route | Meaning |
| --- | --- | --- | --- |
| `reject.json` / `policy.toml` | `rejected` | `direct` | Blocking decider rejection; reviewer is not called. |
| `feedback.json` / `policy.toml` | `feedback` | `feedback` | One reviewer call; synthetic concerns remain non-blocking. |
| `accept.json` / `policy.toml` | `accepted` | `direct` | Direct decider acceptance; reviewer is not called. |
| `accept_feedback.json` / `combined.toml` | `accepted` | `feedback` | PR-text and hard rubric gates pass, then decider acceptance plus non-blocking reviewer findings. |

Every behavior is a separate TOML setting:

- `allow_direct_acceptance` and `allow_direct_rejection` default to false.
  Enable either or both. Their separate confidence thresholds default to 0.95.
  Disabled or insufficient-confidence direct decisions continue to review.
  Unavailable confidence never authorizes a direct decision.
- `review_behavior` defaults to `"escalate"`, preserving standard/deep review
  and human handoff. Set `"feedback"` for one non-blocking review, including
  concerns, uncertainty, and low or unavailable reviewer confidence.
- `feedback_depth` selects `"standard"` (default) or `"deep"` for that review.
- `acceptance_feedback` defaults to false. Enable it to collect feedback after
  direct acceptance, regardless of `review_behavior`. Direct rejection always
  stops immediately. Existing confident skip decisions still skip review.
- `unresolved_outcome` defaults to `"needs_human_review"`. These policies set
  `"rejected"` to block incomplete evidence, failed PR-text/rubric gates,
  provider failures, explicit human requests, and unresolved escalating review
  with route `"blocked"`, avoiding any required human handoff. Feedback findings
  do not block, but failed or malformed feedback responses do.

Try `feedback.json` with no policy: the original standard/deep escalation ends
in `needs_human_review`. Try `examples/pr-text/invalid.json` or
`examples/rubric/hard_blocker.json` with `combined.toml`: the configured gates
block before any provider is called. All existing budget and finding limits
remain available.

Synthetic `MOCK_DECISION_ACCEPT` and `MOCK_DECISION_REJECT` markers in added
lines produce mock decisions with confidence 0.99; rejection wins if both are
present. `MOCK_REVIEW_CONCERN` produces reviewer feedback. These markers exercise
routing and cannot judge code correctness.

Reports remain advisory (`advisory_only: true`); no PR is approved, closed, or
merged. A consumer must enforce `outcome: "rejected"` as blocking. CLI status 0
means a valid report was generated, including rejected reports. `feedback` is
not an acceptance decision. The report schema is now version 3; consumers must
handle the additional outcomes and routes.
