# Contributing

Thank you for your interest in contributing to Triton Control.

## Development Model

Triton Control is an open-source project. Anyone may:

- Open issues
- Suggest features or improvements
- Submit pull requests
- Participate in discussions

Only project maintainers have permission to:

- Merge pull requests
- Push directly to protected branches
- Create releases
- Approve architectural changes

All code changes must be submitted through a pull request.

## Prerequisites

- Python 3.12
- Node.js 22 + npm
- Docker (or Podman)

## Repository Structure

- `triton-frontend/` - Angular frontend
- `triton-backend/` - Python FastAPI backend
- `charts/triton-control/` - Helm chart
- `docs/` - project documentation

## Local Development

Backend:

```bash
cd triton-backend
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\activate
pip install -e ".[dev]"
python main.py
```

Frontend:

```bash
cd triton-frontend
npm ci
npm run generate:api
npm run start:http
```

## Validation Before PR

Backend:

```bash
cd triton-backend
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report --fail-under=75
mypy app/ tests/ scripts/ main.py
ruff check app/ main.py
```

Frontend:

```bash
cd triton-frontend
npm run lint
npm run format:check
npm test -- --watch=false --browsers=ChromeHeadless --code-coverage
npm run test:smoke
```

## Documentation Updates

Update docs when behavior changes:

- `README.md` for high-level workflows or setup changes
- `docs/index.md` for navigation/coverage changes
- `docs/user-guide.md` for user-visible UI/feature changes
- `docs/deployment.md` for runtime/network/deployment behavior changes
- `docs/roadmap.md` for planned (not yet released) work

## Pull Request Guidelines

- Keep PRs focused and scoped.
- Include a clear description of what changed and why.
- Reference any related issue or discussion.
- For UI changes, include screenshots or short notes about visible behavior changes.
- Add or update tests when appropriate.
- If tests were not run, state that explicitly in the PR description.
- Keep documentation updated when introducing user-facing changes.
- Be responsive to review feedback and requested changes.

## Before Opening A Pull Request

For larger features, architectural changes, or significant refactoring:

1. Open an issue or discussion first.
2. Wait for maintainer feedback and agreement on the proposed direction.
3. Submit a pull request only after the proposal has been reviewed and accepted.

Small bug fixes and documentation updates may be submitted directly as pull
requests.

## Branch Protection

The `main` branch is protected.

- Direct pushes to `main` are not allowed.
- At least one maintainer approval is required before merging.
- Maintainers may request changes or close pull requests that do not align with
  project goals.

## Code Of Conduct

Be respectful and constructive in all discussions and reviews.
