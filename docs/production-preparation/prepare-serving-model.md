# Prepare a serving model

Use **Prepare for production** on a development Triton instance to send a served
model version to engineering. You need a member or admin account with access to
the instance and an S3 profile that covers its model repository.

## Submit the served version

1. Open **Serving → Instances**, select the instance, and choose **Prepare for
   production**. The model submission form opens in the engineering queue with
   the S3 repository prefilled.
2. Check the S3 profile and select the model and version. When only one version
   is ready, the form preselects it.
3. Add available evaluation details, including the dataset, baseline, expected
   behavior, and acceptance criteria. You can add missing evaluation inputs
   later by submitting a new revision.
4. Select **Submit to engineering** and wait for capture to finish. The handoff
   records the files for the selected version and its shared model
   configuration. Keep those files available in S3 while engineering needs them.

## Prepare the model in engineering

1. Open **Development → Engineering queue**, select the handoff, and choose
   **Claim task**. Claiming requires engineering group membership.
2. Choose **Open task workspace**. Review the verified inputs and copy files you
   need to change from `/inputs/verified/source/` to `/workspace/conversion`.
3. If you convert the model, save it to versioned S3 storage with **Save
   Converted Model to S3**. The task retains the verified object versions for
   later retrieval.

See [Task workspaces](../task-workspaces.md#save-and-restore-converted-models)
for export and restore details. A deployment created from a task workspace
links back to that task through **Open engineering task**.

## Hand off evidence for approval

Retain validation evidence for the exact artifact and target environment. If
the installation requires security evidence, include the signed security report
with the validation archive. The production approval flow is separate from
submission, task assignment, and workspace testing. See the
[validation and security contracts](../validation-contracts.md#trusted-validation-evidence).
