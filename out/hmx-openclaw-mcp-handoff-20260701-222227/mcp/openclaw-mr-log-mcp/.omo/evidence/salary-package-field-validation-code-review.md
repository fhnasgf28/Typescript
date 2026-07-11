# Code Quality Review: salary-package-field-validation

Date: 2026-06-29
Reviewer role: read-only HMX MR Reviewer / AI Score agent
Reviewed repo: `/home/adminftp/farhan/hashmicro/HMX/hmx-002-salary-package-fix`
Reviewed branch: `fix/salary-package-field-validation`
Upstream: `origin/feat/loan-policy-autocode-clean`
Intended action: local commit of one-file fix, then explicit fast-forward push with `git push origin HEAD:feat/loan-policy-autocode-clean`

## Result

- total_score: 88
- decision: ready_with_cautions
- push_mr_allowed: yes
- codeQualityStatus: WATCH
- recommendation: APPROVE

## Skill Perspective Check

- `hmx-development-mr-reviewer`: loaded and applied for the HMX pre-push score gate.
- `omo:remove-ai-slops`: loaded and applied before judging tests and production code. The diff does not delete tests, loosen tests, add tautological tests, add implementation-mirroring production logic, add needless parsing/normalization, or introduce speculative abstraction. Existing tests assert field validation through the current exception string contract; this is somewhat string-coupled but was pre-existing and directly targets the CI failure.
- `omo:programming`: loaded and applied, including the Python README and code-smells reference. The diff does not introduce untyped escape hatches, broad exception handling, `Any`/`object`, one-off helpers, parameter bloat, or needless abstraction. The touched Python file is 224 pure LOC, below the 250 LOC defect threshold but in the warning band.

## Findings By Severity

### CRITICAL

None.

### HIGH

None.

### MEDIUM

1. Durable verification artifacts are missing.
   - The prompt provided command summaries for focused HMX tests, module upgrade, pre-commit, compileall, and diff-check, but no log artifact paths were available to inspect.
   - I treated those summaries as unverified prompt evidence and lowered `verification_evidence` accordingly.

2. No current pipeline status was provided for the final uncommitted diff.
   - This is not a blocker for a normal source-branch push, but it is an unknown after the planned commit.

### LOW

1. LSP diagnostics are unavailable.
   - The evidence package reports `Transport closed` for LSP diagnostics/status. Static checks, focused HMX tests, and pre-commit evidence partially offset this.

2. Full `core_hr` suite evidence is not clean in the earlier main worktree run.
   - Reported `1168 passed, 14 errors` appears unrelated to salary-package validation, but it is not a green module-wide signal and was not rerun in this clean worktree.

## Positive Observations

- Actual `git status --short --branch` in the reviewed worktree shows only `M hmx/module/basic/core_hr/models/hr_salary_package.py`.
- Actual `git diff --stat` is one file with `6 +++---`.
- The three changed raises at `hmx/module/basic/core_hr/models/hr_salary_package.py:118`, `:131`, and `:151` now use `ValidationError({'code': _CODE_UNIQUE_MSG})`.
- This aligns duplicate-code validation with existing field-mapped salary-package validations at `hmx/module/basic/core_hr/models/hr_salary_package.py:105` and `:107`, and with other HMX/core_hr field-validation patterns found by `rg`.
- Existing tests at `hmx/module/basic/core_hr/tests/test_hr_salary_package_crud.py:55` and `:63` cover duplicate create and duplicate write behavior; no tests were deleted, loosened, or newly added only to mirror constants.
- The change is backend/model-only: no XML, Webx, report, API, migration, security, or browser-visible files are changed in the reviewed clean worktree.
- `git diff --check` produced no output.
- A no-write Python `ast.parse` check of the changed file succeeded.

## Rubric

- verification_evidence: 20/25. Focused test, upgrade, pre-commit, compileall, and diff-check summaries match the changed surface and the reviewer reran read-only diff-check plus AST parse. Points withheld for missing durable log artifact paths, failed/unavailable LSP, no current pipeline status, and no clean full-module suite in this clean worktree.
- hmx_correctness: 24/25. The implementation fixes the reported field-validation contract in all three duplicate-code paths and follows existing HMX field-mapped `ValidationError` usage. No wiring or schema changes are required.
- safety: 18/20. One tracked Python file is modified, no secrets or generated files appear in the reviewed status, and the planned explicit refspec avoids unrelated local worktree commits. Points withheld for remote freshness/pipeline unknowns and the fact that the branch tracks the MR source branch, so operators should avoid a casual push from the wrong worktree.
- mr_hygiene: 12/15. The evidence package clearly explains the isolated worktree, branch, intended push command, touched surface, and verification commands. Points withheld for missing durable evidence paths, missing current pipeline status, and no MR template/dependency/ticket evidence.
- maintainability: 14/15. The change is minimal, scoped, idiomatic for this file, and introduces no abstraction or slop. One point withheld because the file is already in the 200-250 pure LOC warning band and the relevant tests assert the exception string rather than a structured field-error property.

## Hard Blockers

None.

## Missing Evidence

- Durable log paths/artifacts for the reported HMX focused test, upgrade, pre-commit, compileall, and pre-patch reproduction runs.
- Current pipeline status for the final post-commit SHA.
- LSP diagnostics for the changed Python file.
- Clean full `core_hr` suite run from this clean worktree, or a durable accepted waiver for the unrelated expense/limit-package errors.

## Required Fixes Before Push/MR

None. The cautions above do not rise to hard blockers for this backend-only one-file fix.

## Recommended Followups After Push/MR

- Attach or retain durable verification logs for the focused HMX test, module upgrade, and pre-commit run.
- Confirm the remote branch has not advanced before pushing; use the planned explicit refspec and no force push.
- Check the MR pipeline after push.
- If HMX exposes structured validation-error fields, consider updating future tests to assert that structure instead of relying on `str(exception)` containing `'code'`.

## Reviewer Commands Run

- Read `hmx-development-mr-reviewer`, `omo:remove-ai-slops`, `omo:programming`, Python README, and code-smells skill references.
- Attempted `codegraph_explore`; codegraph reported no `.codegraph/` index for this worktree, so shell inspection was used.
- `git status --short --branch`
- `git branch -vv`
- `git diff --stat`
- `git diff --check`
- `git diff -- hmx/module/basic/core_hr/models/hr_salary_package.py`
- `git diff --name-status`
- `git diff --numstat -- hmx/module/basic/core_hr/models/hr_salary_package.py`
- `git rev-parse --abbrev-ref --symbolic-full-name @{u}`
- `git merge-base --is-ancestor @{u} HEAD`
- `git log --oneline --decorate @{u}..HEAD`
- `git ls-files --others --exclude-standard -- .env '*/.env'`
- `nl -ba` on the changed model, relevant salary-package tests, neighboring validation models/tests, and `hmx/hmx/exceptions.py`
- `rg` for field-mapped `ValidationError` patterns and conflict markers
- `awk` pure LOC measurement for the changed file
- `python3` with `ast.parse(...)` against `hmx/module/basic/core_hr/models/hr_salary_package.py`

## Stale Context Warning

Learned context and prior review artifacts are stale for this clean worktree. I relied on the actual current worktree status/diff and treated prompt-only verification summaries conservatively. Remote branch state and pipeline state may also be stale because I did not run `git fetch` or query GitLab from this read-only review.
