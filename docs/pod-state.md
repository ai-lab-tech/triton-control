# Structured pod state (S11-A / concepts issue 51)

`GET /api/instances/{id}/pods` authorizes instance access before Kubernetes I/O.
Managed instances return desired/updated/ready/available counts, generation and
observed generation, Deployment conditions, owned pod identities and container
states. Deployment → ReplicaSet → Pod controller UIDs establish ownership; labels
are not authority. Old rollout ReplicaSets and terminating pods remain visible.
A generation/UID change during observation returns 409; observations are not an
atomic Kubernetes snapshot. Stale controller status sets `awaiting_observation`.

Pod `phase`, Ready condition and deletion timestamp are separate observations.
Container waiting/termination reasons, restart counts, previous termination reason,
init containers and scheduling conditions preserve image/init/GPU failure evidence.
`revision` is the Kubernetes ReplicaSet rollout revision. `release` is null until
an authoritative serving-release binding exists; image tags are not release IDs.
Container restart counts are per container and are not inferred from phase.

External instances return cached endpoint health and no Kubernetes counts/pods.
Provider failures return 502; missing managed Deployment returns 404. This endpoint
does not silently convert provider errors into healthy or empty observations.

No database migration is needed. Existing instance `pod_statuses` and logs remain
compatible. Helm adds read-only ReplicaSet get/list privileges; custom installations
must provide those alongside existing Deployment/pod reads in managed namespaces.
The endpoint is read-only and is eligible under the production capability policy.
UI tables, selectable logs, scaling and model-aware readiness remain downstream.

Validation:

```sh
cd triton-backend
.venv/bin/python -m unittest tests.test_pod_state -v
# Real Kubernetes integration against a disposable environment with an existing Deployment:
POD_STATE_TEST_NAMESPACE=test POD_STATE_TEST_DEPLOYMENT=triton \
  .venv/bin/python -m unittest tests.test_pod_state.LivePodStateTests -v
```

Live cluster verification is required before closing the planning issue. The local
configured API was unreachable during implementation. Unit tests cover controller
UID exclusions, stale generations, read races, terminating pods, Running versus
Ready, init/image/GPU diagnostics, denied access and external endpoints. No SQL
writes/concurrent database transitions are introduced by this read endpoint.
