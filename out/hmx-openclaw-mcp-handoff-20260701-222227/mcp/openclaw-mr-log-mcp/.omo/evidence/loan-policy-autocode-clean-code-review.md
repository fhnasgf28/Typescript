# Code Quality Review: loan-policy-autocode-clean

Date: 2026-06-29
Repo: /home/adminftp/farhan/hashmicro/HMX/hmx-002
Branch: feat/loan-policy-autocode-clean
Intended action: local commit, then push to origin HEAD:feat/loan-policy-autocode-clean

## Skill Perspective Check

- hmx-development-mr-reviewer: loaded and applied for the HMX pre-push AI Score gate.
- omo:remove-ai-slops: loaded and applied as a review lens. The Python production diff does not add needless abstraction, deletion-only tests, tautological tests, or production parsing/normalization beyond the existing business validation path. The current XML diff does contain stale/inaccurate explanatory comments around menu ordering. The newly observed untracked menu-order test mirrors implementation constants and is not a substitute for browser-visible E2E evidence.
- omo:programming: loaded, including the Python README. The Python diff does not introduce untyped escape hatches, new helpers, broad exception handling, or brittle implementation-mirroring production logic. The touched Python file is 224 pure LOC, which is in the warning band but below the hard 250 LOC defect threshold.

## Findings By Severity

### CRITICAL

1. Current diff contains browser-visible XML menu changes that were omitted from the evidence package and lack exact E2E script coverage plus current-run video evidence.
   - Evidence package claimed one changed file and no browser-visible changes.
   - Actual `git diff --name-only` shows:
     - `hmx/module/basic/core_hr/models/hr_salary_package.py`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml`
     - `hmx/module/basic/core_hr/views/hr_dashboard_views.xml`
     - `hmx/module/basic/core_hr_intelligence/views/menu_item.xml`
   - Browser-visible changed lines include:
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:8`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:18`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:22`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:32`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:43`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:56`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:81`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:125`
     - `hmx/module/basic/core_hr/views/base_menu_views.xml:138`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:10`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:13`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:16`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:19`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:22`
     - `hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml:37`
     - `hmx/module/basic/core_hr/views/hr_dashboard_views.xml:19`
     - `hmx/module/basic/core_hr_intelligence/views/menu_item.xml:14`
   - HMX MR reviewer policy makes browser-visible changes without exact module-owned E2E script coverage and current-run video evidence a hard blocker. The score is capped below passing and push/MR is not allowed.

2. Final status check revealed additional unaccounted local state beyond the supplied evidence package.
   - `hmx/module/basic/core_hr/tests/__init__.py:83` imports `test_hr_root_menu_order`.
   - `hmx/module/basic/core_hr/tests/test_hr_root_menu_order.py` is untracked. If only tracked changes are committed, the tracked import can create a missing-module failure after push.
   - The same status output also shows untracked `.ci-generated-pre-commit-config.yaml` and a large untracked `.ci-main/` tree. These are not part of the evidence package and should not be accidentally swept into a product push.

### HIGH

None beyond the CRITICAL blocker.

### MEDIUM

1. Expanded `core_hr` suite evidence is not fully green.
   - Supplied evidence says `hmx run_test --init=core_hr` ended with `1168 passed, 14 errors`.
   - The reported failures are in expense/limit-package tests and appear unrelated to the salary package change, but the suite is still not clean evidence for the whole module.

2. XML menu resequencing comments are stale or inconsistent with the changed values.
   - Example: `hmx/module/basic/core_hr/views/base_menu_views.xml:21` says Attendance moves to sequence 30, while the changed menu value is sequence 6 at line 22.
   - Example: `hmx/module/basic/core_hr/views/base_menu_views.xml:42` says Payroll moves to sequence 60, while the changed menu value is sequence 9 at line 43.
   - This is not the gating failure by itself, but it increases review risk if the XML changes are intended to ship.

3. The untracked menu-order test is brittle and implementation-mirroring.
   - `hmx/module/basic/core_hr/tests/test_hr_root_menu_order.py:6` through `:19` hard-codes XML IDs, labels, and sequence values, then asserts those same constants at lines `:22` through `:26`.
   - This may catch raw data drift, but it does not verify the browser-visible menu rendering, role visibility, or navigation behavior required by the HMX gate.

### LOW

1. LSP diagnostics are unavailable.
   - Supplied evidence says both LSP diagnostics and status failed with "Transport closed".
   - Pre-commit, focused tests, compileall, and diff-check partially offset this for the Python file, but no LSP signal is available.

2. Current pipeline status and MR dependency/description evidence were not provided.
   - This is not a hard blocker for a local follow-up push to an existing feature branch, but it lowers MR hygiene confidence.

## Positive Observations

- The Python diff in `hmx/module/basic/core_hr/models/hr_salary_package.py:118`, `:131`, and `:151` is narrowly scoped.
- The change aligns duplicate-code validation with the same field-mapped `ValidationError({'code': ...})` pattern already used for required/alphanumeric code validation in the same file at `hmx/module/basic/core_hr/models/hr_salary_package.py:105` and `:107`.
- Existing tests at `hmx/module/basic/core_hr/tests/test_hr_salary_package_crud.py:55` and `:63` cover duplicate create and duplicate write field validation. The tests were not loosened or deleted.
- `git diff --check` was run in this review and produced no output.

## Reviewer Commands Run

- `sed -n` on the HMX MR reviewer, remove-ai-slops, programming, and Python programming skill files.
- `git -c safe.directory='*' status --porcelain=v1 --branch --untracked-files=all`
- `git -c safe.directory='*' status --porcelain=v1 --branch --untracked-files=normal`
- `git -c safe.directory='*' branch -vv`
- `git -c safe.directory='*' diff --name-only`
- `git -c safe.directory='*' diff --name-status`
- `git -c safe.directory='*' diff --stat`
- `git -c safe.directory='*' diff -- hmx/module/basic/core_hr/models/hr_salary_package.py`
- `git -c safe.directory='*' diff -- hmx/module/basic/core_hr/views/base_menu_reconstruct_views.xml hmx/module/basic/core_hr/views/base_menu_views.xml hmx/module/basic/core_hr/views/hr_dashboard_views.xml hmx/module/basic/core_hr_intelligence/views/menu_item.xml`
- `git -c safe.directory='*' diff --check`
- `nl -ba` on the changed Python model, relevant salary package test, changed XML files, and untracked `test_hr_root_menu_order.py`.
- `rg` for field-mapped `ValidationError` patterns in core HR.
- `awk` pure LOC count for `hmx/module/basic/core_hr/models/hr_salary_package.py`, result 224.

## HMX AI Score

- verification_evidence: 7/25. Focused salary package evidence is good, but the current diff contains browser-visible XML changes with no E2E script/video evidence. The expanded module suite is not fully green, LSP is unavailable, no current pipeline evidence was supplied, and the final status shows additional unaccounted dirty/untracked files.
- hmx_correctness: 15/25. The Python fix is correct and follows existing HMX field validation patterns. The unaccounted XML menu ordering changes and untracked menu-order test are not validated by the supplied evidence.
- safety: 8/20. The branch tracks a feature branch rather than the target/shared Human-Resources branch, and no secrets were found in the inspected diff. Safety is materially reduced by unaccounted browser-visible changes, a tracked import of an untracked test file, unaccounted generated/CI artifacts, and failed expanded suite evidence.
- mr_hygiene: 3/15. The evidence package materially misstates the current diff, dirty state, and touched surfaces. MR dependency, current pipeline, and browser evidence are missing.
- maintainability: 7/15. The Python change is simple and maintainable. The XML comments are stale against the changed menu sequence values, and the untracked menu-order test mirrors implementation constants instead of behavior.

Total score: 40/100
Decision: blocked
Push/MR allowed: no

## Required Fixes Before Push/MR

1. Either remove/isolate the four XML menu files from the current push scope, or provide exact module-owned E2E script coverage plus current-run video evidence for the menu/dashboard/intelligence ordering changes.
2. Resolve the unaccounted local state: `hmx/module/basic/core_hr/tests/__init__.py`, the untracked `test_hr_root_menu_order.py`, `.ci-generated-pre-commit-config.yaml`, and the untracked `.ci-main/` tree must be intentionally included with evidence or removed from the push scope.
3. If the XML changes remain, replace or supplement the implementation-mirroring menu-order test with required browser-visible evidence and update stale comments so they match the intended menu order and changed sequence values.
4. Regenerate the evidence package from the actual current `git status --short --branch`, `git diff --name-only`, and touched surfaces.
5. Re-run the HMX MR reviewer gate after the final diff is settled.

## Recommendation

REQUEST_CHANGES. The salary package Python fix appears acceptable in isolation, but the current working diff is not the one described by the evidence package and includes browser-visible XML changes that trigger the HMX hard blocker.
