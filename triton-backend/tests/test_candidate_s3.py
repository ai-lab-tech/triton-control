"""S3 capture and retained object-version behavior, with deterministic provider fakes."""

import hashlib
import io
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError, ReadTimeoutError

from app.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from app.services.candidates.artifacts import CaptureLimits, S3ArtifactStore, S3Source
from tests import test_candidate_import as fixtures


class MemoryS3:
    """Model version and conditional-read semantics, not Triton execution."""

    def __init__(self, versioned=True):
        self.versioned = versioned
        self.objects = {}
        self.counter = 0
        self.bodies = []
        self.calls = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        data = Body.read() if hasattr(Body, "read") else Body
        self.counter += 1
        version = str(self.counter) if self.versioned else None
        self.objects.setdefault((Bucket, Key), []).append((version, bytes(data)))
        return {"VersionId": version} if version else {}

    def get_bucket_versioning(self, Bucket):
        return {"Status": "Enabled" if self.versioned else "Suspended"}

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix, **kwargs):
        contents = []
        for (bucket, key), versions in sorted(self.objects.items()):
            if bucket == Bucket and key.startswith(Prefix):
                data = versions[-1][1]
                contents.append({"Key": key, "Size": len(data), "ETag": self.etag(data)})
        for offset in range(0, max(1, len(contents)), 2):
            yield {"Contents": contents[offset : offset + 2]}

    @staticmethod
    def etag(data):
        return '"' + hashlib.sha256(data).hexdigest() + '"'

    def head_object(self, Bucket, Key):
        version, data = self.objects[(Bucket, Key)][-1]
        result = {"ContentLength": len(data), "ETag": self.etag(data)}
        if version:
            result["VersionId"] = version
        return result

    def get_object(self, Bucket, Key, **kwargs):
        self.calls.append((Key, kwargs))
        versions = self.objects[(Bucket, Key)]
        selected = versions[-1]
        if "VersionId" in kwargs:
            selected = next(v for v in versions if v[0] == kwargs["VersionId"])
        if "IfMatch" in kwargs and kwargs["IfMatch"] != self.etag(selected[1]):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        body = io.BytesIO(selected[1])
        self.bodies.append(body)
        return {"Body": body, "ContentLength": len(selected[1]), "VersionId": selected[0]}


def upload_source(client, root, bucket="source", prefix="prepared/"):
    for path in root.rglob("*"):
        if path.is_file():
            client.put_object(Bucket=bucket, Key=prefix + path.relative_to(root).as_posix(), Body=path.read_bytes())


class CandidateS3Tests(fixtures.CandidateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = MemoryS3()
        upload_source(self.client, self.source)

    def source_provider(self):
        return S3Source(self.client, "source", "prepared")

    def test_versioned_source_and_destination_preserve_original_bytes_and_object_origins(self):
        store = S3ArtifactStore(self.client, "artifacts")
        result = self.do_import(source=self.source_provider(), store=store)
        self.assertEqual(result.source_receipt["bucket"], "source")
        self.assertTrue(all(item["version_id"] for item in result.source_receipt["objects"].values()))
        self.assertTrue(all(item["version_id"] for item in result.artifacts.reference["objects"]))
        self.assertTrue(all(body.closed for body in self.client.bodies))
        # Overwriting a key creates another version; reads remain bound to the old one.
        item = result.artifacts.reference["objects"][0]
        self.client.put_object(Bucket="artifacts", Key=item["key"], Body=b"replacement")
        store.verify(result.artifacts, result.inventory, result.metadata_inventory, CaptureLimits())
        self.assertEqual(self.do_import(source=self.source_provider(), store=store), result)

    def test_unversioned_source_uses_conditional_get_and_computes_its_own_hash(self):
        self.client = MemoryS3(versioned=False)
        upload_source(self.client, self.source)
        result = self.do_import(source=self.source_provider())
        self.assertEqual(result.source_receipt["kind"], "s3")
        self.assertTrue(all("IfMatch" in args for _, args in self.client.calls))
        self.assertTrue(all(entry.digest.startswith("sha256:") for entry in result.inventory.files))

    def test_unversioned_destination_is_rejected_without_publishing_candidate(self):
        store = S3ArtifactStore(MemoryS3(versioned=False), "artifacts")
        with self.assertRaises(BadRequestError):
            self.do_import(source=self.source_provider(), store=store)
        self.assertEqual(self.counts(), (0, 0))

    def test_s3_path_escapes_collisions_and_noncanonical_names_are_rejected(self):
        for relative in ("../escape", "iris/../../escape", "iris//1/model.onnx", "iris/1/bad\\name"):
            key = "prepared/" + relative
            self.client.put_object(Bucket="source", Key=key, Body=b"unsafe")
            with self.subTest(relative=relative), self.assertRaises(BadRequestError):
                self.do_import(source=self.source_provider())
            del self.client.objects[("source", key)]
        with self.assertRaises(BadRequestError):
            S3Source(self.client, "source", "../unsafe")

    def test_source_mutation_during_capture_is_rejected(self):
        original = self.client.get_object
        changed = False

        def mutate(**kwargs):
            nonlocal changed
            result = original(**kwargs)
            if not changed:
                changed = True
                self.client.put_object(Bucket=kwargs["Bucket"], Key=kwargs["Key"], Body=b"source changed")
            return result

        with patch.object(self.client, "get_object", side_effect=mutate), self.assertRaises(ConflictError):
            self.do_import(source=self.source_provider())
        self.assertEqual(self.counts(), (0, 0))
        self.assertTrue(all(body.closed for body in self.client.bodies))

    def test_changed_source_between_listing_and_head_is_rejected(self):
        original = self.client.head_object

        def mutate(**kwargs):
            self.client.put_object(Bucket=kwargs["Bucket"], Key=kwargs["Key"], Body=b"changed")
            return original(**kwargs)

        with patch.object(self.client, "head_object", side_effect=mutate), self.assertRaises(ConflictError):
            self.do_import(source=self.source_provider())

    def test_s3_failures_return_sanitized_errors_and_no_database_publication(self):
        error = ClientError({"Error": {"Code": "AccessDenied", "Message": "private credential detail"}}, "GetObject")
        with patch.object(self.client, "get_object", side_effect=error):
            with self.assertRaises(ServiceUnavailableError) as raised:
                self.do_import(source=self.source_provider())
        self.assertNotIn("private credential", str(raised.exception))
        self.assertEqual(self.counts(), (0, 0))

    def test_ambiguous_storage_write_retains_no_registered_candidate_and_retry_recovers(self):
        store = S3ArtifactStore(self.client, "artifacts")
        original = self.client.put_object

        def ambiguous(**kwargs):
            original(**kwargs)
            raise ReadTimeoutError(endpoint_url="https://fixture.invalid")

        with patch.object(self.client, "put_object", side_effect=ambiguous), self.assertRaises(ServiceUnavailableError):
            self.do_import(source=self.source_provider(), store=store)
        self.assertEqual(self.counts(), (0, 0))
        result = self.do_import(source=self.source_provider(), store=store)
        self.assertEqual(result.candidate.origin, fixtures.request().candidate_origin)
        self.assertEqual(self.counts(), (1, 1))

    def test_missing_version_receipt_is_not_accepted_as_immutable_storage(self):
        store = S3ArtifactStore(self.client, "artifacts")
        original = self.client.put_object

        def no_version(**kwargs):
            original(**kwargs)
            return {}

        with patch.object(self.client, "put_object", side_effect=no_version), self.assertRaises(ConflictError):
            self.do_import(source=self.source_provider(), store=store)
        self.assertEqual(self.counts(), (0, 0))

    def test_source_and_destination_prefixes_must_not_overlap(self):
        with self.assertRaises(BadRequestError):
            self.do_import(
                source=self.source_provider(), store=S3ArtifactStore(self.client, "source", "prepared/output")
            )

    def test_file_directory_collisions_are_rejected(self):
        self.client.put_object(Bucket="source", Key="prepared/iris", Body=b"collides with model directory")
        with self.assertRaises(BadRequestError):
            self.do_import(source=self.source_provider())

    def test_directory_and_s3_inventory_order_are_identical(self):
        (self.source / "iris-2/1").mkdir(parents=True)
        (self.source / "iris-2/config.pbtxt").write_bytes(b'name: "iris-2" backend: "python"')
        (self.source / "iris-2/1/model.py").write_bytes(b"# structural fixture")
        upload_source(self.client, self.source)
        local = self.do_import()
        remote = self.do_import(source=self.source_provider())
        self.assertEqual(local.inventory, remote.inventory)
