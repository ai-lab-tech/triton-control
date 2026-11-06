"""Deployment examples stay in the database and are exposed to inference clients."""

import unittest
from datetime import datetime

from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import TritonInstanceEntity
from app.mappers.instance import entity_to_dto
from app.schemas import CreateDeploymentRequest


class DeploymentExampleTests(unittest.TestCase):
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
            )
            second = TritonInstanceEntity(name="second", url="http://second", s3_bucket="same", s3_prefix="repo")
            session.add(first)
            session.add(second)
            session.commit()
            session.expire_all()
            dto = entity_to_dto(session.get(TritonInstanceEntity, first.id))
            self.assertEqual(dto.example_model_name, "model")
            self.assertEqual(dto.example_request, payload)
            self.assertIsNone(session.get(TritonInstanceEntity, second.id).example_request)
