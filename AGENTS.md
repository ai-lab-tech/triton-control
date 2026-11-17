# Coding Agent Instructions

These instructions apply to the entire repository.

## Repository language

All content added to this repository must be in English. This includes code
identifiers, comments, docstrings, documentation, agent instructions, user-facing
text, error messages, test descriptions, and commit messages. Apply the same rule
to new or rewritten text in existing files. The language used in conversation with
the user does not change this requirement.

## Required local CI checks before every push

The verification rule below is quoted from
[GitLab Runner's AGENTS.md, Verification](https://github.com/gitlabhq/gitlab-runner/blob/main/AGENTS.md#verification):

> After making changes, always run these in order before pushing. All must pass:

For this repository, use the applicable backend and frontend checks listed below,
in the order defined by their CI workflows. GitLab Runner's Go-specific commands
do not apply here.

The hook rule is quoted from
[Apache Infrastructure Actions' AGENTS.md, Pre-commit checks](https://github.com/apache/infrastructure-actions/blob/main/AGENTS.md#pre-commit-checks-prek):

> do **not** skip hooks with `--no-verify`.

Apply this rule to any configured hooks in this repository. If a check rewrites
files, inspect and commit the corrections, then rerun the check successfully
before pushing. The following repository-specific requirements implement these
rules for Triton Control:

- Before every push, read the workflows in `.github/workflows/` and determine
  which jobs apply to the entire diff being pushed. Do not consider only the last
  commit or individual changed tests.
- Run the applicable CI checks locally first, using the same commands, working
  directories, tool versions, configuration, and coverage thresholds as CI.
  Workflow files are the authoritative source.
- Do not stop after unit tests pass. Include type checking, linting, formatting,
  architecture checks, security scans, generated-file checks, and applicable
  integration and smoke tests.
- Install missing tools and dependencies in an isolated environment where
  possible, and provision the required local test services.
- Do not push with failing checks. Fix the cause and rerun the affected checks.
  Do not bypass failures by weakening CI rules, coverage thresholds, or tests.
- If a required check cannot run locally, identify the exact check, the blocker,
  and the remaining uncertainty. Complete all other checks. Do not push without
  explicit approval for that exception; a general request to push does not
  authorize skipping checks.
- Results must apply to the final code. After further code changes, rerun the
  affected checks. Before pushing, also run `git diff --check` and review the
  complete diff being pushed.
- Report passed, failed, and skipped checks separately. Only claim checks passed
  if they were actually run. Local success does not mean GitHub CI has passed.

## Backend

For backend changes, run the complete `unit-tests` job from
`.github/workflows/backend-ci.yml` locally. It currently includes:

- Dependency auditing and checked-in OpenAPI specification verification
- The full unit test suite, coverage threshold, and coverage summary
- `mypy app/ tests/ scripts/ main.py`
- `ruff check app/ main.py`
- `lint-imports`
- `bandit -r app/ main.py`

Also run applicable PostgreSQL, S3, Kubernetes, Helm, contract, and extension tests
from the workflows according to the changes being pushed.

## Frontend

For frontend changes, follow `.github/workflows/frontend-ci.yml`, including
dependency auditing, security scanning, API generation, ESLint, Prettier, unit
tests with coverage, and applicable smoke and end-to-end tests.

## Documentation-only changes

When a push changes only documentation or agent instructions and no workflow
requires checks for those files, unrelated backend and frontend test suites are
not required. Check workflow triggers and references, and run `git diff --check`.
This exception does not apply to code changes included in the same push.
