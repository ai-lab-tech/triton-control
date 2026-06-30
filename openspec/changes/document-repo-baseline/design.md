## Context

Triton Control is a management platform for NVIDIA Triton Inference Server. The repo includes an Angular frontend, FastAPI backend, SQLModel/PostgreSQL persistence, S3-compatible model storage, Kubernetes deployment flows, Development workspaces, code-server extension packaging, Perf Analyzer, MLflow, Argo Workflows proxying, local auth, and optional OIDC.

The baseline should describe the system that exists before new feature work starts. It is intended to prevent feature proposals from mixing current-state discovery with new requirements.

## Goals / Non-Goals

**Goals:**

- Produce a repo-wide baseline that maps Triton Control's major subsystems and ownership boundaries.
- Trace the core runtime flows that future changes depend on.
- Identify docs and tests that currently define expected behavior.
- Make the baseline usable as pre-work for later feature changes, including Triton repository scaffolding.

**Non-Goals:**

- Add new user-facing product behavior.
- Refactor frontend, backend, deployment, or extension code.
- Replace existing architecture documentation wholesale.
- Define the detailed design for any one downstream feature.

## Decisions

### Keep baseline separate from feature proposals

The repo baseline belongs in its own change so downstream feature changes can reference it without absorbing unrelated documentation work.

Alternative considered: keep a baseline section inside every feature design. That repeats broad system context and makes feature scopes harder to review.

### Document boundaries by subsystem and flow

The baseline should combine two views: subsystem ownership and runtime flows. Subsystem ownership explains where code lives; runtime flows explain how requests and state move across boundaries.

Alternative considered: only list directories. That is easy to produce but not enough to guide architectural decisions.

### Reuse existing docs as source material

Existing architecture, user, deployment, development-workspace, security, and troubleshooting docs should anchor the baseline. The baseline should reconcile those docs with current code paths instead of inventing a parallel model.

Alternative considered: derive everything only from code. That improves precision but risks losing product-level context already captured in docs.

## Risks / Trade-offs

- Baseline can become stale -> Keep it concise, tie it to file paths, and update it when major subsystem boundaries change.
- Baseline can become too broad to review -> Organize it by subsystem and flow, with explicit non-goals.
- Documentation may disagree with code -> Treat mismatches as findings to resolve during baseline tasks.
- Future feature changes may still duplicate context -> Reference the baseline change rather than copying large sections into feature designs.

## Migration Plan

No runtime migration is required. The baseline can be implemented as documentation and planning artifacts. Rollback is removing or reverting those documentation changes.

## Open Questions

- Should the final baseline live only in OpenSpec artifacts, or should part of it be promoted into `docs/architecture-overview.md` and related docs?
- Should future OpenSpec feature templates include a short "Baseline dependency" section?
