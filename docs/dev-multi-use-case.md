# Multi-use-case development with Argo CD

Run one Triton Control release per development use case. Give each release its
own namespace, hostname, MLflow server and namespaced Argo Workflows controller.
All releases use one development PostgreSQL server. Use two databases and two
logins per release, for example `control_a` and `mlflow_a`. See the
[deployment diagram](deployment.md#recommended-kubernetes-deployment-model).

## Who owns which YAML?

| Repository | Owner | Contents |
| --- | --- | --- |
| Platform GitOps | Platform team | ApplicationSets, AppProjects, namespace assignments, chart revision, platform image versions and shared Helm settings |
| Triton Control | Platform maintainers | Helm chart and its pinned Argo Workflows dependency |
| One repository per use case | Use-case team | Workload definitions and parameters under `dev/workloads/`, such as the labels expected by a model |

For team A, Argo CD creates two Applications in the same destination namespace:

- **`triton-ai-dept-a`** renders the centrally configured chart into `ai-dept-a`. This Application also manages the dedicated `ai-dept-a-builds` namespace.
- **`ai-dept-a-workloads`** reads only `dev/workloads/` from team A's repository. A merge to that repository updates A's workload definitions. It cannot supply Helm values to the platform Application.

## Install the platform

1. Install Argo CD with the ApplicationSet controller. Install the Argo Workflows
   CRDs once for the cluster at a version compatible with the pinned subchart.
   Each release has `argoWorkflows.crds.install: false`.
2. Create the use-case namespaces, DNS records and databases. Provision these
   Secrets through your secret manager in **each** application namespace:

    | Secret | Contents for `ai-dept-a` |
    | --- | --- |
    | `control-app` | `DATABASE_URL` for `control_a`, stable `SESSION_SECRET`, `JWT_SECRET` and `S3_SECRET_ENCRYPTION_KEY` |
    | `mlflow-postgres` | `database-url` for `mlflow_a`, using its dedicated login |
    | `triton-tls` | TLS certificate and key for A's ingress hostname |

3. Copy the [platform ApplicationSet](../examples/gitops/argocd/dev-applicationset.yaml)
   into your platform GitOps repository. Set your ingress hosts and published
   image coordinates. The example uses the Argo CD `default` project for the
   platform. Only platform administrators should be able to change or deploy
   this configuration. The chart creates namespaced resources, a build namespace
   per release and cluster RBAC for namespace discovery.
4. Sync the platform ApplicationSet through your Argo CD bootstrap Application.
   Once the releases are healthy, open each Triton Control instance, configure
   its S3 profile and install MLflow. Check an instance's tracking UI and confirm
   its first experiment appears in that use case's database. See
   [managed MLflow storage](deployment.md#managed-mlflow-storage).

## Connect the team repositories

1. Copy the [example workload](../examples/gitops/argocd/use-case-repo/dev/workloads/check-labels.yaml)
   into each team's Git repository at `dev/workloads/check-labels.yaml`. The
   example registers an Argo WorkflowTemplate that validates model labels.
   Syncing it installs the definition. An authorized native Argo submission
   starts a run.
2. In the **platform** repository, create one AppProject per team using the
   [team A project](../examples/gitops/argocd/dept-a-workloads-project.yaml)
   as a template. For B and C change the project name, repo URL and destination
   namespace. Each project permits only its team's source, namespace and
   `WorkflowTemplate` resources. Review changes to team templates as executable
   code. Native Argo submissions run outside Triton Control's submission checks.
3. Copy the [workloads ApplicationSet](../examples/gitops/argocd/dev-workloads-applicationset.yaml)
   into the platform repository. Replace its example URLs with the matching
   team repo URLs. Register read credentials in Argo CD for private repositories.
   Sync the projects first, then the workloads ApplicationSet.
4. Verify onboarding for A with `argocd app get ai-dept-a-workloads` and
   `kubectl -n ai-dept-a get workflowtemplate check-labels`. A team can now
   update its label list by merging a change to its own repository's `main`.

## Roll out a platform fix

| Component | Central change | How it reaches existing Dev installations |
| --- | --- | --- |
| Triton Control | Publish the tested image and update its tag in the platform ApplicationSet. Pin a new chart revision when chart changes are required. | Argo CD syncs each platform Application and Kubernetes rolls the deployment. |
| Argo Workflows | Pin the tested chart revision containing the updated Argo subchart. Manage matching cluster CRDs centrally, following the release's upgrade order. | Each platform Application updates its namespaced controller and server. |
| MLflow | Update `mlflow.version` for servers and `development.mlflow.version` for new workspaces in the platform ApplicationSet. | After the backend rollout, the platform operator calls the authenticated admin endpoint `POST /api/mlflow/upgrade` on each installation with an existing MLflow server. Verify the running image and tracking readiness. |

For a staged rollout, validate the change in a pilot release before changing
the shared template for all teams. The example's automated sync applies a change
to that shared template across all three releases. No team-repository edit is
needed for any of these platform updates.
