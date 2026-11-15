# Builder qualification evidence — 2026-11-14

**All 15 checks passed on the isolated Calico-enabled Minikube profile.**
`calico-2026-11-14.json` is the passing report; `policy-calico-2026-11-14.json`
is its exact policy input. The report's fixture hash matches the checked-in CLI,
runner and Job renderer. This qualifies that exact executor/platform/fixture policy,
not arbitrary Dockerfiles or different site credentials/registry ACLs.

The earlier `minikube-2026-11-14.json` remains as negative evidence: the existing
cluster passed 14 checks but failed network isolation. Its forbidden registry
port remained reachable despite the NetworkPolicy. That installation remains
unqualified; the passing Calico report does not override its different policy hash.
Builds remain disabled by default.

The fixture used real Kubernetes Jobs, S3 staging, authenticated private base pull,
scoped registry push, a forbidden repository push, separate runtime pull, filesystem
exclusion, executor/registry digest comparison, cancellation, deadline enforcement,
no service-account token or host socket, pod policy and actual network denial.
Namespace and staged-object cleanup passed too.

The isolated cluster initially hit the host inotify limit (128). With explicit user
approval, the limit was temporarily raised to 1024. Calico then started successfully.
Its actual pod allocation was `10.244.65.192/26`; using the node's advertised
`10.244.0.0/24` blocked legitimate registry access. The corrected policy was rerun
from a fresh namespace and passed all checks.

Validation from implementation: 789 backend tests, six skipped, 81.2% coverage;
eight focused tests with real disposable PostgreSQL, including concurrent startup
and evidence inserts. Ruff, mypy, architecture contracts, Bandit and Helm checks passed.
No application code changed for this final network-enforcement retest.

Cleanup verified: the isolated profile was deleted, `fs.inotify.max_user_instances`
was restored to 128, the current context remains `minikube`, and temporary
credential/kubeconfig files were removed.
