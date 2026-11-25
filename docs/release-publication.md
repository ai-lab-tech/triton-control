# Publish a frozen release

Run `triton-publish-release` from the installed backend in the publisher's environment. The command copies version-pinned S3 model files into production storage and publishes metadata either to Git or S3. Production must be able to pull the release's existing digest-pinned runtime and materializer images. Registry replication, when required, must preserve those digests.

Supply an operator-owned directory containing the following exports.

| File | Contents |
| --- | --- |
| `release.json`, `target.json` | ServingRelease and explicit ServingTarget. The target selects the canonical input release digest. |
| `plan.json` | MaterializationPlan with both inventories, normalization and source object versions. The release repository URI selects the source bucket and prefix. Its materializer image must match the image pinned in the plan. |
| `candidate.json`, `lineage.json` | ServingCandidateV1 and the exact referenced release lineage manifest. |
| `inventory-*.json` | Additional inventories needed by the candidate's source snapshot or attempts. |
| `validation-archive.json` | EvidenceArchive with the independently signed envelope, validation manifest and passing report. |
| `security.json` | The referenced security document when the release declares a security gate. |

Provide the materializer budget file with `max_bytes`, `max_file_bytes` and `deadline_seconds`. Allow temporary disk for both the source and prepared model files. Both destination buckets require versioning. Grant the publisher read access to specific source versions and write access to the authorized destination prefixes. Restrict other writers, deny deletion of retained versions, and configure retention for as long as a release can be deployed. Give the production reconciler read-only credentials. For Git, protect the publication branch against deletion and force pushes and configure the publisher's Git identity.

Publish metadata to an existing Git branch using the normal Git and AWS credentials of the publisher:

```sh
triton-publish-release ./frozen --limits budgets.json \
  --model-bucket production-models --model-prefix serving \
  git https://git.example.org/serving/releases.git proposed-releases
```

Publish metadata to a versioned S3 prefix:

```sh
triton-publish-release ./frozen --limits budgets.json \
  --model-bucket production-models --model-prefix serving \
  s3 production-metadata releases
```

Select `target-<target-name>.json` explicitly in the reconciler's MetadataSource. To replace an existing pointer, pass `--previous-target sha256:…` before the provider with the reviewed canonical digest of the existing target. The CLI prints the published target digest. A stale target or a conflicting immutable release fails the publication. Rerun the same command after an interruption. Concurrent S3 conditional writes or a rejected Git fast-forward may require a retry.

The publisher reads the source versions named in the frozen plan, even if the live source prefix has changed. It verifies destination object versions and hashes, retains the original manifest and materialization plan, and writes a destination plan containing the new version IDs. The receipt at `receipt-<target-digest-without-prefix>.json` locates both plans, the original release, inventories, candidate and validation archive relative to the target. The release also carries the exact runtime digest and references the exported lineage and evidence. Git records ordered commits and publishes them with one fast-forward push. On S3 the pointer is the final conditional write. Consumers verify the release and evidence against local trust policy, storage bindings and independent approval before deployment.

A publication reserves both the publisher label and the lineage origin for the release. If an earlier publisher wrote releases without origin records, replay those frozen bundles before publishing new labels to establish their origin records. Publication adds files under publisher-selected prefixes and uses the existing serving and materialization contracts.
