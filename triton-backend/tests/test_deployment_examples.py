"""Deployment examples stay in the database and are exposed to inference clients."""

import unittest
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import TritonInstanceEntity
from app.mappers.instance import entity_to_dto
from app.schemas import CreateDeploymentRequest
from app.schemas.deployment import MAX_EXAMPLE_BYTES
from app.services.deployment import examples
from app.services.deployment.examples import collect_deployment_examples


class DeploymentExampleTests(unittest.TestCase):
    def instance(self, prefix="repo"):
        return SimpleNamespace(s3_enabled=True, s3_bucket="models", s3_prefix=prefix)

    @patch("app.services.deployment.examples.require_s3_client")
    def test_form_example_takes_priority_without_any_repository_access(self, client):
        payload: dict[str, Any] = {"inputs": []}
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

    def test_oversized_manual_example_is_rejected(self):
        # Arrange
        payload = {"data": "x" * MAX_EXAMPLE_BYTES}

        # Act / Assert
        with self.assertRaises(ValidationError):
            self.request(example_model_name="model", example_request=payload)

    @patch("app.services.deployment.examples.require_s3_client")
    def test_deeply_nested_optional_json_is_skipped_and_stream_closed(self, factory):
        # Arrange
        body = BytesIO(b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}')
        factory.return_value.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        factory.return_value.get_object.return_value = {"Body": body}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        self.assertTrue(body.closed)

    @patch("app.services.deployment.examples.require_s3_client")
    def test_optional_stream_oserror_is_skipped_and_stream_closed(self, factory):
        # Arrange
        body = Mock()
        body.read.side_effect = OSError("Stream unavailable")
        factory.return_value.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        factory.return_value.get_object.return_value = {"Body": body}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        body.close.assert_called_once()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_oversized_stream_without_content_length_has_bounded_reads(self, factory):
        # Arrange
        source = BytesIO(b'{"data":"' + b'x' * (MAX_EXAMPLE_BYTES * 2) + b'"}')
        body = Mock(wraps=source)
        factory.return_value.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        factory.return_value.get_object.return_value = {"Body": body}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        self.assertTrue(all(0 < call.args[0] <= 16 * 1024 for call in body.read.call_args_list))
        self.assertEqual(sum(call.args[0] for call in body.read.call_args_list), MAX_EXAMPLE_BYTES + 1)
        body.close.assert_called_once()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_large_content_length_is_skipped_before_reading(self, factory):
        # Arrange
        body = Mock()
        factory.return_value.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        factory.return_value.get_object.return_value = {"Body": body, "ContentLength": MAX_EXAMPLE_BYTES + 1}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        body.read.assert_not_called()
        body.close.assert_called_once()

    @patch("app.services.deployment.examples.require_s3_client")
    def test_repository_listing_is_bounded_and_uses_short_timeouts_without_retries(self, factory):
        # Arrange
        client = factory.return_value
        client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": True, "NextContinuationToken": "next"}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        self.assertEqual(client.list_objects_v2.call_count, examples.MAX_LIST_PAGES)
        config = factory.call_args.kwargs["client_config"]
        self.assertEqual(config.connect_timeout, examples.S3_TIMEOUT_SECONDS)
        self.assertEqual(config.read_timeout, examples.S3_TIMEOUT_SECONDS)
        self.assertEqual(config.retries, {"total_max_attempts": 1})

    @patch("app.services.deployment.examples.require_s3_client")
    def test_model_attempts_are_bounded_even_when_examples_are_missing(self, factory):
        # Arrange
        client = factory.return_value
        client.list_objects_v2.return_value = {"Contents": [
            {"Key": f"repo/model-{index}/config.pbtxt"} for index in range(examples.MAX_MODELS + 5)
        ]}
        client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        self.assertEqual(client.get_object.call_count, examples.MAX_MODELS)

    @patch("app.services.deployment.examples.monotonic", side_effect=[0, 6])
    @patch("app.services.deployment.examples.require_s3_client")
    def test_expired_budget_does_not_start_s3_operations(self, factory, _clock):
        # Arrange
        client = factory.return_value

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        client.list_objects_v2.assert_not_called()
        client.get_object.assert_not_called()

    @patch("app.services.deployment.examples.MAX_TOTAL_BYTES", 50)
    @patch("app.services.deployment.examples.require_s3_client")
    def test_aggregate_byte_budget_includes_invalid_examples(self, factory):
        # Arrange
        client = factory.return_value
        client.list_objects_v2.return_value = {"Contents": [
            {"Key": f"repo/model-{index}/config.pbtxt"} for index in range(5)
        ]}
        client.get_object.side_effect = lambda **_: {"Body": BytesIO(b'x' * 30)}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        self.assertEqual(client.get_object.call_count, 2)

    def test_legacy_example_is_translated_at_api_boundary_without_mutating_storage(self):
        # Arrange
        legacy = TritonInstanceEntity(
            id=1, name="legacy", url="http://legacy", example_model_name="model", example_request={"inputs": []},
        )

        # Act
        dto = entity_to_dto(legacy)

        # Assert
        self.assertEqual(dto.inference_examples, {"model": {"inputs": []}})
        self.assertEqual(legacy.inference_examples, {})

    @patch("app.services.deployment.examples.MAX_TOTAL_BYTES", 40)
    @patch("app.services.deployment.examples.require_s3_client")
    def test_snapshot_size_budget_includes_model_names_and_json_structure(self, factory):
        # Arrange
        client = factory.return_value
        client.list_objects_v2.return_value = {"Contents": [
            {"Key": f"repo/model-{index}/config.pbtxt"} for index in range(3)
        ]}
        client.get_object.side_effect = lambda **_: {"Body": BytesIO(b'{"inputs":[]}')}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {"model-0": {"inputs": []}})

    @patch("app.services.deployment.examples.monotonic", side_effect=[0, 0, 0, 6])
    @patch("app.services.deployment.examples.require_s3_client")
    def test_time_budget_expiring_before_stream_read_closes_body(self, factory, _clock):
        # Arrange
        body = Mock()
        factory.return_value.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        factory.return_value.get_object.return_value = {"Body": body}

        # Act
        result = collect_deployment_examples(self.request(), self.instance())

        # Assert
        self.assertEqual(result, {})
        body.read.assert_not_called()
        body.close.assert_called_once()

    def test_canonical_example_wins_over_legacy_storage(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1, name="mixed", url="http://mixed", example_model_name="model", example_request={"id": "legacy"},
            inference_examples={"model": {"id": "canonical"}},
        )

        # Act
        dto = entity_to_dto(instance)

        # Assert
        self.assertEqual(dto.inference_examples, {"model": {"id": "canonical"}})

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
