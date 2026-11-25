"""Release publication conformance and integration with real versioned storage."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.canonical import canonical_bytes
from app.schemas.materialization import MaterializationPlan, StorageBinding
from app.schemas.publication import PublicationReceipt
from app.schemas.validation import EvidenceEnvelope, ValidationManifest, ValidationReport
from app.services.gate_evidence import package_evidence, signing_message
from app.services.materialization.kubernetes import manifests
from app.services.publication.cli import main
from app.services.publication.models import _upload
from app.services.publication.service import publish
from app.services.publication.storage import S3Store, checksum
from app.services.serving_metadata import load_metadata
from app.services.validation import artifact_subjects, document_digest
from tests.materialization_fixtures import IMAGE, LIMITS, policy
from tests.test_validation_contracts import ROOT, data

FIXTURES = ROOT / "contracts/serving/v1alpha1/fixtures"
ENDPOINT = os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT")


class ReceiptSchemaTest(unittest.TestCase):
    def test_export_matches_schema(self):
        schema = ROOT / "contracts/publication/v1/PublicationReceipt.schema.json"
        self.assertEqual(json.loads(schema.read_bytes()), PublicationReceipt.model_json_schema())


class TransferDeadlineTest(unittest.TestCase):
    def test_expired_multipart_upload_is_aborted_before_completion(self):
        client = Mock()
        client.create_multipart_upload.return_value = {"UploadId": "upload"}
        client.upload_part.return_value = {"ETag": "part"}
        location = dict(Bucket="destination", Key="model")
        root = Path(self.enterContext(TemporaryDirectory()))
        file = root / "model"
        file.write_bytes(b"model")

        with patch("app.services.publication.models.MULTIPART_BYTES", 4), patch(
            "app.services.publication.models.time.monotonic", side_effect=[0, 1, 3]
        ), self.assertRaises(TimeoutError):
            _upload(client, location, file, deadline=2)

        client.upload_part.assert_called_once()
        client.abort_multipart_upload.assert_called_once_with(**location, UploadId="upload")
        client.complete_multipart_upload.assert_not_called()


@unittest.skipUnless(ENDPOINT, "requires disposable versioned S3")
class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.client = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id="candidate_test",
                                   aws_secret_access_key="candidate_test_password", region_name="us-east-1")
        self.source, self.destination = ["publication-" + uuid4().hex for _ in range(2)]
        for bucket in (self.source, self.destination):
            self.client.create_bucket(Bucket=bucket)
            self.client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        self.store = S3Store(self.client, self.destination, "metadata")
        files = FIXTURES / "artifacts"
        for name in ("candidate.json", "lineage.json"):
            (self.root / name).write_bytes((files / name).read_bytes())
        inventory = json.loads((files / "inventory.json").read_bytes())
        self.weights = (files / "weights.txt").read_bytes()
        path = inventory["files"][0]["path"]
        location = dict(Bucket=self.source, Key="source/" + path)
        version = self.client.put_object(**location, Body=self.weights)["VersionId"]
        self.client.put_object(**location, Body=b"changed development bytes")
        self.write("plan", {"materializer_image": IMAGE, "source_inventory": inventory, "prepared_inventory": inventory,
                            "objects": [{"path": path, "version_id": version}]})
        release = data("release")
        spec = release["spec"]
        spec["repository"].update(uri=f"s3://{self.source}/source/", materializerImage=IMAGE)
        manifest_data = data("manifest")
        manifest_data["repository"] = spec["repository"]
        manifest = ValidationManifest.model_validate(manifest_data)
        subjects = artifact_subjects(manifest).model_dump(by_alias=True)
        report_data = data("report-passed")
        report_data.update(manifestDigest=document_digest(manifest), subjects=subjects)
        report = ValidationReport.model_validate(report_data)
        env = data("evidence")
        env["subjects"] = subjects
        env["report"]["digest"] = document_digest(report)
        envelope = EvidenceEnvelope.model_validate(env)
        key = Ed25519PrivateKey.generate()
        archive = package_evidence(manifest, report, envelope, key.sign(signing_message(envelope)).hex())
        (self.root / "validation-archive.json").write_bytes(archive)
        spec["validation"].update(subjects=subjects, digest=document_digest(envelope))
        self.write("release", release)
        self.target = json.loads((FIXTURES / "target.json").read_bytes())
        self.point_target()

    def write(self, name, value):
        (self.root / (name + ".json")).write_bytes(canonical_bytes(value))

    def point_target(self):
        self.target["spec"]["release"]["digest"] = load_metadata((self.root / "release.json").read_bytes()).digest
        self.write("target", self.target)

    def run_publish(self, store=None, previous=None):
        return publish(self.root, store or self.store, self.client, self.destination, "published", LIMITS, previous)

    def get(self, key, **options):
        with self.client.get_object(Bucket=self.destination, Key=key, **options)["Body"] as stream:
            return stream.read()

    pointer_key = "metadata/target-toy-production.json"

    def interrupt_release_write(self):
        write = self.store.write

        def interrupt(key, value):
            if key.startswith("release-"):
                raise InterruptedError()
            write(key, value)

        return patch.object(self.store, "write", side_effect=interrupt)

    def test_interruption_leaves_target_absent(self):
        with self.interrupt_release_write(), self.assertRaises(InterruptedError):
            self.run_publish()

        with self.assertRaises(ClientError) as absent:
            self.get(self.pointer_key)
        self.assertEqual(absent.exception.response["ResponseMetadata"]["HTTPStatusCode"], 404)

    def test_retry_finishes_interrupted_publication(self):
        with self.interrupt_release_write(), self.assertRaises(InterruptedError):
            self.run_publish()

        result = self.run_publish()

        self.assertEqual(load_metadata(self.get(self.pointer_key)).digest, result)

    def test_completed_publication_is_idempotent(self):
        expected = self.run_publish()
        version = self.client.head_object(Bucket=self.destination, Key=self.pointer_key)["VersionId"]

        result = self.run_publish()

        self.assertEqual(result, expected)
        self.assertEqual(self.client.head_object(Bucket=self.destination, Key=self.pointer_key)["VersionId"], version)

    def test_frozen_versions_produce_a_deployable_plan_after_source_changes(self):
        plan = json.loads((self.root / "plan.json").read_bytes())
        self.client.put_object(Bucket=self.source, Key="source/unrelated", Body=b"new")
        self.client.delete_object(Bucket=self.source, Key="source/" + plan["objects"][0]["path"])

        result = self.run_publish()

        receipt = PublicationReceipt.model_validate_json(self.get("metadata/receipt-" + result[7:] + ".json"))
        copied = MaterializationPlan.model_validate_json(self.get("metadata/" + receipt.published_plan))
        release = load_metadata(self.get("metadata/release-toy.r1.json")).document
        prefix = release.spec.repository.uri.removeprefix(f"s3://{self.destination}/").rstrip("/")
        reference = copied.objects[0]
        self.assertEqual(self.get(prefix + "/" + reference.path, VersionId=reference.version_id), self.weights)
        self.assertEqual(self.get("metadata/" + receipt.validation_archive),
                         (self.root / "validation-archive.json").read_bytes())
        binding = StorageBinding(bucket=self.destination, prefix=prefix, secret_name="storage")
        resources = manifests(release, copied, policy(), binding, name="toy", namespace="test", limits=LIMITS)
        self.assertIn("Pod", [resource["kind"] for resource in resources])

    def test_missing_materializer_fails_before_model_transfer(self):
        release = json.loads((self.root / "release.json").read_bytes())
        release["spec"]["repository"].pop("materializerImage")
        release["spec"]["validation"]["subjects"]["materializerImage"] = None
        self.write("release", release)
        self.point_target()

        with self.assertRaisesRegex(ValueError, "plan and release inputs differ"):
            self.run_publish()

        self.assertEqual(self.client.list_objects_v2(Bucket=self.destination)["KeyCount"], 0)

    def test_target_update_rejects_stale_digest(self):
        expected = self.run_publish()
        self.target["spec"]["replicas"] = 2
        self.point_target()

        with self.assertRaisesRegex(ValueError, "Publication conflict"):
            self.run_publish(previous="sha256:" + "0" * 64)

        self.assertEqual(load_metadata(self.get(self.pointer_key)).digest, expected)

    def test_target_update_accepts_canonical_digest_of_formatted_json(self):
        formatted = json.dumps(self.target, indent=2).encode()
        self.client.put_object(Bucket=self.destination, Key=self.pointer_key, Body=formatted)

        result = self.run_publish(previous=load_metadata(formatted).digest)

        self.assertEqual(load_metadata(self.get(self.pointer_key)).digest, result)

    def test_origin_cannot_be_reused_under_a_different_label(self):
        expected = self.run_publish()
        release = json.loads((self.root / "release.json").read_bytes())
        release["metadata"]["release"] = "r2"
        self.write("release", release)
        self.point_target()

        with self.assertRaisesRegex(ValueError, "Publication conflict at origin-"):
            self.run_publish(previous=expected)

        self.assertEqual(load_metadata(self.get(self.pointer_key)).digest, expected)

    def test_label_cannot_be_reused_for_a_different_origin(self):
        expected = self.run_publish()
        release = json.loads((self.root / "release.json").read_bytes())
        release["spec"]["lineage"]["origin"]["record_id"] = "conflicting-release"
        self.write("lineage", release["spec"]["lineage"])
        release["spec"]["lineageManifest"]["digest"] = checksum((self.root / "lineage.json").read_bytes())
        self.write("release", release)
        self.point_target()

        with self.assertRaisesRegex(ValueError, "Publication conflict at identity-"):
            self.run_publish(previous=expected)

        self.assertEqual(load_metadata(self.get(self.pointer_key)).digest, expected)

    def test_unauthorized_publisher_cannot_create_target(self):
        anonymous = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                                 config=Config(signature_version=UNSIGNED))
        self.addCleanup(anonymous.close)
        with self.assertRaises(ClientError) as denied:
            S3Store(anonymous, self.destination, "metadata").write("target-toy.json", b"unauthorized")
        self.assertEqual(denied.exception.response["ResponseMetadata"]["HTTPStatusCode"], 403)
        with self.assertRaises(ClientError) as absent:
            self.get("metadata/target-toy.json")
        self.assertEqual(absent.exception.response["ResponseMetadata"]["HTTPStatusCode"], 404)

    def test_git_publication_order_and_receipt(self):
        repo = self.root / "git"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        env = {
            "GIT_AUTHOR_NAME": "Test Publisher", "GIT_COMMITTER_NAME": "Test Publisher",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "AWS_ACCESS_KEY_ID": "candidate_test", "AWS_SECRET_ACCESS_KEY": "candidate_test_password",
            "AWS_ENDPOINT_URL_S3": str(ENDPOINT), "AWS_DEFAULT_REGION": "us-east-1",
        }
        formatted = json.dumps(self.target, indent=2).encode()
        (repo / "target-toy-production.json").write_bytes(formatted)
        remote = self.root / "remote.git"
        self.write("limits", {"max_bytes": 1024**2, "max_file_bytes": 1024**2, "deadline_seconds": 30})
        args = ["triton-publish-release", str(self.root), "--limits", str(self.root / "limits.json"),
                "--model-bucket", self.destination, "--model-prefix", "published",
                "--previous-target", load_metadata(formatted).digest, "git", str(remote), "main"]
        with patch.dict(os.environ, env), patch.object(sys, "argv", args):
            subprocess.run(["git", "add", "target-toy-production.json"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "Initial"], cwd=repo, check=True)
            subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(remote)], check=True)
            status = main()
        self.assertEqual(status, 0)
        target = subprocess.check_output(["git", "show", "main:target-toy-production.json"], cwd=remote)
        result = load_metadata(target).digest
        history = subprocess.check_output(["git", "log", "--format=%s", "-3"], cwd=remote, text=True).splitlines()
        self.assertEqual(history, ["Publish target-toy-production.json", "Publish receipt-" + result[7:] + ".json",
                                   "Publish release-toy.r1.json"])
