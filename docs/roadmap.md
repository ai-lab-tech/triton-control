# Product Roadmap

This roadmap outlines planned features that are not yet active in the current release.

## Focus

- Better scaling and operations for Triton deployments
- Higher productivity in the workspace
- Clearer administration and security options

## Planned Enhancements

### 1) Deployment, Operations, and Performance

#### Parallel Execution for Perf Analyzer

Today:

- Perf Analyzer runs as a singleton workload.
- Profiling runs are executed sequentially.

Goal:

- Enable parallel profiling runs.
- Replace the singleton model with isolated Kubernetes Jobs per run.
- Support multiple concurrent runs across different instance/model targets.

#### Perf Analyzer Profile Page Links

Goal:

- Link Perf Analyzer status and run history directly to the related model Profile pages.
- Make it easier to reopen an active or completed profiling run from dashboard and Perf Analyzer views.

#### Kubernetes Events in Dashboard

Goal:

- Show relevant Kubernetes events for self-deployed Triton instances in the dashboard.
- Surface deployment, pod scheduling, image pull, readiness, and failure events without requiring users to open raw cluster tooling.

#### Central Backend Logs in Dashboard

Goal:

- Provide a central backend log view directly from the dashboard.
- Help administrators inspect backend errors, warnings, authentication events, and operational activity without opening container or cluster logs manually.

Scope:

- Show recent backend log entries with timestamp, level, source, and message.
- Avoid exposing secrets, credentials, tokens, or sensitive request payloads in the UI.

#### Resource Management for Triton Instances

Goal:

- Provide clear management of GPU, RAM, and CPU resources for self-deployed Triton instances during deployment.
- Enable explicit configuration of requests and limits per self-deployed Triton instance.

### 2) Security and Networking

#### TLS Configuration in the Deployment Flow

Today:

- Add Deployment supports ingress host/class configuration.
- HTTPS TLS is managed outside Triton Control
  (for example via cert-manager resources or pre-created TLS secrets).

Goal:

- Add TLS configuration directly in the Add Deployment UI.
- Support cert-manager (Issuer/ClusterIssuer) and existing TLS secrets.

#### Ingress Authentication Enhancements

Goal:

- Extend ingress configuration with HTTP Basic Authentication support.
- Add support for Bearer token authentication.

Scope:

- Both authentication options apply to ingress endpoints of self-deployed Triton serving instances created via Triton Control.

#### OIDC Provider Examples

Goal:

- Keep OIDC documentation and architecture provider-neutral.
- Add tested setup examples for common OIDC providers without making one provider part of the product architecture.

#### Local Email/Password Account Lifecycle

Today:

- Local email/password authentication supports bootstrap, admin-created users, and self-registration.
- New self-registered users remain pending until an admin approves them.
- There is no built-in email invite flow.
- There is no built-in password reset or forgot-password flow.
- There is no email verification step for local accounts.


Goal:

- Add optional email-based invite and account recovery flows for local users.
- Reduce admin overhead when onboarding users and handling password loss.


Scope:

- Invite a user by email with a one-time activation link.
- Let invited users set their initial password and complete account activation.
- Add forgot-password and reset-password flows with expiring single-use tokens.
- Add configurable SMTP delivery and email templates.
.

### 3) Platform and Workspace

#### OpenAI API Support for Triton Server

Goal:

- Provide OpenAI API support as an integration option for Triton server workflows.

#### User Management with Groups

Goal:

- Extend user management with groups.
- Establish a foundation for role- and team-based administration.

#### code-server Integration

Goal:

- Integrate a browser-based development environment directly into the workspace.
- Provide every user with their own isolated development environment.



#### Enhanced S3 Browser

Goal:

- Enable deleting files in the S3 Browser.
- Enable renaming files in the S3 Browser.

## Prioritization

### Next

- code-server integration

- Perf Analyzer parallel execution
- Perf Analyzer profile page links
- Kubernetes events in dashboard
- Central backend logs in dashboard
- TLS configuration in the Add Deployment flow
- OIDC provider examples
- Local email/password invite and password reset flow
- OpenAI API integration for Triton Server

### Later

- Resource management for Triton deployments (GPU/RAM/CPU)
- User management with groups
- S3 Browser: file delete and rename
