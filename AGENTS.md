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
in the order defined by their CI workflows, with the integration and smoke test
exception below. GitLab Runner's Go-specific commands do not apply here.

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
- Run the applicable fast CI checks locally first, using the same commands, working
  directories, tool versions, configuration, and coverage thresholds as CI.
  Workflow files are the authoritative source.
- Do not stop after unit tests pass. Include type checking, linting, formatting,
  architecture checks, security scans, and generated-file checks.
- Install missing tools and dependencies in an isolated environment where
  possible. Do not provision full integration environments merely to permit a push.
- Do not push with failing checks. Fix the cause and rerun the affected checks.
  Do not bypass failures by weakening CI rules, coverage thresholds, or tests.
- If a required local check cannot run locally, identify the exact check, the blocker,
  and the remaining uncertainty. Complete all other checks. Do not push without
  explicit approval for that exception; a general request to push does not
  authorize skipping checks.
- Results must apply to the final code. After further code changes, rerun the
  affected checks. Before pushing, also run `git diff --check` and review the
  complete diff being pushed.
- Report passed, failed, and skipped checks separately. Only claim checks passed
  if they were actually run. Local success does not mean GitHub CI has passed.

## Long-running integration and smoke tests

Run long-running integration, smoke, and end-to-end tests in GitHub CI after
pushing, rather than requiring them locally before a push. This includes jobs
that provision PostgreSQL, MinIO/S3, Kubernetes/Kind clusters, or complete
application stacks. Deferring these jobs to GitHub CI is explicitly authorized
and does not require a separate approval before each push.

Keep fast unit tests (including mocked integration tests), coverage, type checks,
linting, formatting, security checks, generated-file verification, and lightweight
contract and Helm rendering checks in the local pre-push gate. Do not use this
exception to bypass a known test failure or weaken CI.

After pushing, check the GitHub CI runs for the pushed commit. Report deferred
checks as pending until CI completes, fix failures caused by the changes, and do
not claim the PR is ready to merge until all required GitHub checks pass.

## Backend

For backend changes, run the complete `unit-tests` job from
`.github/workflows/backend-ci.yml` locally. It currently includes:

- Dependency auditing and checked-in OpenAPI specification verification
- The full unit test suite, coverage threshold, and coverage summary
- `mypy app/ tests/ scripts/ main.py`
- `ruff check app/ main.py`
- `lint-imports`
- `bandit -r app/ main.py`

Run applicable lightweight Helm rendering, contract, and extension tests locally.
Leave long-running PostgreSQL, S3, Kubernetes, and full-stack integration jobs to
GitHub CI as described above.

## Frontend

For frontend changes, follow `.github/workflows/frontend-ci.yml`, including
dependency auditing, security scanning, API generation, ESLint, Prettier, unit
tests with coverage. Run `npm run generate:api -- --fresh` before pushing frontend
or generator changes so an existing JAR cannot hide broken download sources.
Run long-running smoke and end-to-end tests in GitHub CI.

## Documentation-only changes

When a push changes only documentation or agent instructions and no workflow
requires checks for those files, unrelated backend and frontend test suites are
not required. Check workflow triggers and references, and run `git diff --check`.
This exception does not apply to code changes included in the same push.
