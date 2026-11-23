"""Release publication conformance and integration with real versioned storage."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.canonical import canonical_bytes
from app.schemas.publication import PublicationReceipt
from app.schemas.validation import EvidenceEnvelope, ValidationManifest, ValidationReport
from app.services.gate_evidence import package_evidence, signing_message
from app.services.publication.cli import main
from app.services.publication.service import publish
from app.services.publication.storage import S3Store, checksum
from app.services.serving_metadata import load_metadata
from app.services.validation import artifact_subjects, document_digest
from tests.materialization_fixtures import IMAGE, LIMITS
from tests.test_validation_contracts import ROOT, data

FIXTURES = ROOT / "contracts/serving/v1alpha1/fixtures"
ENDPOINT = os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT")


class ReceiptSchemaTest(unittest.TestCase):
    def test_export_matches_schema(self):
        schema = ROOT / "contracts/publication/v1/PublicationReceipt.schema.json"
        self.assertEqual(json.loads(schema.read_bytes()), PublicationReceipt.model_json_schema())


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
        spec["repository"]["uri"] = f"s3://{self.source}/source/"
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

    def get(self, key):
        with self.client.get_object(Bucket=self.destination, Key=key)["Body"] as stream:
            return stream.read()

    def test_interruption_cross_bucket_replay_and_conflicts(self):
        original = self.store.write

        def interrupt(key, value, previous=None):
            if key.startswith("release-"):
                raise InterruptedError()
            original(key, value, previous)

        with patch.object(self.store, "write", side_effect=interrupt), self.assertRaises(InterruptedError):
            self.run_publish()
        pointer_key = "metadata/target-toy-production.json"
        with self.assertRaises(ClientError):
            self.get(pointer_key)
        result = self.run_publish()
        self.assertEqual(self.run_publish(), result)
        self.assertEqual(load_metadata(self.get(pointer_key)).digest, result)
        receipt = PublicationReceipt.model_validate_json(self.get("metadata/receipt-" + result[7:] + ".json"))
        copied = json.loads(self.get("metadata/" + receipt.published_plan))
        file = copied["source_inventory"]["files"][0]
        self.assertEqual(self.get("published/models/" + next(iter(receipt.inventories))[7:] + "/" + file["path"]),
                         self.weights)
        self.assertEqual(self.get("metadata/" + receipt.validation_archive),
                         (self.root / "validation-archive.json").read_bytes())
        self.target["spec"]["replicas"] = 2
        self.point_target()
        with self.assertRaisesRegex(ValueError, "Publication conflict"):
            self.run_publish()
        updated = self.run_publish(previous=result)
        release = json.loads((self.root / "release.json").read_bytes())
        release["spec"]["lineage"]["origin"]["record_id"] = "conflicting-release"
        self.write("lineage", release["spec"]["lineage"])
        release["spec"]["lineageManifest"]["digest"] = checksum((self.root / "lineage.json").read_bytes())
        self.write("release", release)
        self.point_target()
        with self.assertRaisesRegex(ValueError, "Publication conflict"):
            self.run_publish(previous=updated)
        self.assertEqual(load_metadata(self.get(pointer_key)).digest, updated)

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
        remote = self.root / "remote.git"
        self.write("limits", {"max_bytes": 1024**2, "max_file_bytes": 1024**2, "deadline_seconds": 30})
        args = ["triton-publish-release", str(self.root), "--limits", str(self.root / "limits.json"),
                "--model-bucket", self.destination, "--model-prefix", "published", "git", str(remote), "main"]
        with patch.dict(os.environ, env), patch.object(sys, "argv", args):
            subprocess.run(["git", "commit", "--allow-empty", "-qm", "Initial"], cwd=repo, check=True)
            subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(remote)], check=True)
            self.assertEqual(main(), 0)
        target = subprocess.check_output(["git", "show", "main:target-toy-production.json"], cwd=remote)
        result = load_metadata(target).digest
        history = subprocess.check_output(["git", "log", "--format=%s", "-3"], cwd=remote, text=True).splitlines()
        self.assertEqual(history, ["Publish target-toy-production.json", "Publish receipt-" + result[7:] + ".json",
                                   "Publish release-toy.r1.json"])
