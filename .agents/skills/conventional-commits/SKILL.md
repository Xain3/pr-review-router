---
name: conventional-commits
description: 'Create clear Conventional Commit messages for this repository, explain non-obvious rationale and changes, and report breaking changes. Use when preparing, reviewing, or making a commit.'
---

# Conventional Commits

Prepare or make a commit that follows this repository's commit guidelines.

## Procedure

1. Read the repository's `CONTRIBUTING.md` commit guidelines and inspect `git status` plus the staged and unstaged diffs. Do not assume all working-tree changes belong to this commit.
2. Identify the logical change being committed. If the intended files or scope are unclear, ask before staging or committing. Preserve unrelated and pre-existing changes.
3. Write the subject as `type(scope): description`, with an optional scope. Use a conventional type such as `feat`, `fix`, `docs`, `test`, `refactor`, `build`, or `ci`; keep the description concise and imperative.
4. When the purpose or impact is not apparent from the subject and diff, add a body that explains the rationale and summarizes the changes. Keep the body factual and specific.
5. Check whether the change breaks compatibility. If it does, mark the subject with `!` after the type or scope and add a `BREAKING CHANGE:` footer that describes the incompatibility. Report the breaking change explicitly in the completion summary as well.
6. If asked to make the commit, stage only the intended files, review the staged diff, and commit with the prepared message. Otherwise, provide the proposed message without committing.
7. Report the resulting commit hash when a commit was made, and mention any checks or relevant limitations without claiming checks that were not run.

## Message Examples

Straightforward change:

```text
docs: clarify offline example behavior
```

Non-obvious change:

```text
fix(router): preserve coverage details on handoff

Keep the original coverage findings so operators can identify which inputs
need attention after the review is handed to a human.
```

Breaking change:

```text
feat(config)!: require explicit provider selection

BREAKING CHANGE: configurations without a provider now fail validation and
must specify one before review can run.
```