"""Deployment examples stay in the database and are exposed to inference clients."""

import unittest
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import TritonInstanceEntity
from app.mappers.instance import entity_to_dto
from app.schemas import CreateDeploymentRequest
from app.services.deployment.examples import collect_deployment_examples


class DeploymentExampleTests(unittest.TestCase):
    def instance(self, prefix="repo"):
        return SimpleNamespace(s3_enabled=True, s3_bucket="models", s3_prefix=prefix)

    @patch("app.services.deployment.examples.require_s3_client")
    def test_form_example_takes_priority_without_any_repository_access(self, client):
        payload = {"inputs": []}
        result = collect_deployment_examples(
            self.request(example_model_name="model", example_request=payload), self.instance(),
        )
        self.assertEqual(result, {"model": payload})
        client.assert_not_called()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_repository_examples_are_read_for_each_model_without_writes(self, factory):
        client = factory.return_value
        client.list_objects_v2.side_effect = [
            {"Contents": [{"Key": "repo/a/config.pbtxt"}], "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": [{"Key": "repo/b/config.pbtxt"}, {"Key": "repo/a/1/artifact/config.pbtxt"}]},
        ]
        client.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(
            b'{"inputs": [], "id": "' + kwargs["Key"].encode() + b'"}',
        )}
        result = collect_deployment_examples(self.request(), self.instance())
        self.assertEqual(result, {
            "a": {"inputs": [], "id": "repo/a/request.json"},
            "b": {"inputs": [], "id": "repo/b/request.json"},
        })
        self.assertEqual(client.get_object.call_count, 2)
        client.put_object.assert_not_called()
        client.delete_object.assert_not_called()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_model_root_prefix_is_supported(self, factory):
        client = factory.return_value
        client.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        client.get_object.return_value = {"Body": BytesIO(b'{"inputs": []}')}
        result = collect_deployment_examples(self.request(model_name="model"), self.instance("repo/model"))
        self.assertEqual(result, {"model": {"inputs": []}})
        client.get_object.assert_called_once_with(Bucket="models", Key="repo/model/request.json")

    @patch("app.services.deployment.examples.require_s3_client")
    def test_missing_invalid_and_nonfinite_examples_are_ignored(self, factory):
        client = factory.return_value
        for payload in (b"invalid", b"[]", b'{"x":NaN}', None):
            with self.subTest(payload=payload):
                client.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
                client.get_object.side_effect = None
                if payload is None:
                    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
                else:
                    client.get_object.return_value = {"Body": BytesIO(payload)}
                self.assertEqual(collect_deployment_examples(self.request(), self.instance()), {})
        client.put_object.assert_not_called()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_unavailable_optional_repository_does_not_block_deployment(self, factory):
        factory.return_value.list_objects_v2.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "List")
        self.assertEqual(collect_deployment_examples(self.request(), self.instance()), {})

    def request(self, **kwargs):
        return CreateDeploymentRequest(
            deployment_name="example", image="triton:test", s3_url="s3://http://s3/models/repo",
            s3_access_key="key", s3_secret_key="secret", **kwargs,
        )

    def test_example_requires_a_safe_target_model(self):
        for name in (None, "../other", "model/nested", "..", ""):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.request(example_model_name=name, example_request={"inputs": []})

    def test_nonfinite_json_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.request(example_model_name="model", example_request={"x": float("nan")})

    def test_database_examples_are_isolated_by_deployment_and_returned_in_dto(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        payload = {"inputs": [{"name": "x", "shape": [1], "datatype": "INT64", "data": [42]}]}
        with Session(engine) as session:
            first = TritonInstanceEntity(
                name="first", url="http://first", s3_bucket="same", s3_prefix="repo",
                example_model_name="model", example_request=payload, created_at=datetime.now(),
                inference_examples={"model": payload},
            )
            second = TritonInstanceEntity(name="second", url="http://second", s3_bucket="same", s3_prefix="repo")
            session.add(first)
            session.add(second)
            session.commit()
            session.expire_all()
            dto = entity_to_dto(session.get(TritonInstanceEntity, first.id))
            self.assertEqual(dto.example_model_name, "model")
            self.assertEqual(dto.example_request, payload)
            self.assertEqual(dto.inference_examples, {"model": payload})
            self.assertIsNone(session.get(TritonInstanceEntity, second.id).example_request)
