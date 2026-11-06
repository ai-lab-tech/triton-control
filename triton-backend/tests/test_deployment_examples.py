"""Example requests are optional and saved only for the selected repository model."""

import json
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.exceptions import BadGatewayError, BadRequestError
from app.schemas import CreateDeploymentRequest
from app.services.deployment.example_request import save_deployment_example


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

    @patch("app.services.deployment.example_request.build_s3_client")
    def test_absent_example_does_not_touch_repository(self, build):
        save_deployment_example(self.request())
        build.assert_not_called()

    @patch("app.services.deployment.example_request.build_s3_client")
    def test_example_saved_beside_matching_model_config(self, build):
        client = build.return_value
        client.list_objects_v2.return_value = {"Contents": [
            {"Key": "repo/other/config.pbtxt"}, {"Key": "repo/model/config.pbtxt"},
        ]}
        payload = {"inputs": [{"name": "x", "shape": [1], "datatype": "INT64", "data": [42]}]}
        save_deployment_example(self.request(example_model_name="model", example_request=payload))
        args = client.put_object.call_args.kwargs
        self.assertEqual(args["Bucket"], "models")
        self.assertEqual(args["Key"], "repo/model/request.json")
        self.assertEqual(json.loads(args["Body"]), payload)
        self.assertEqual(args["ContentType"], "application/json")

    @patch("app.services.deployment.example_request.build_s3_client")
    def test_model_root_prefix_does_not_duplicate_model_folder(self, build):
        client = build.return_value
        client.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        request = self.request(example_model_name="model", example_request={"inputs": []})
        request.s3_url += "/model"
        save_deployment_example(request)
        self.assertEqual(client.put_object.call_args.kwargs["Key"], "repo/model/request.json")

    @patch("app.services.deployment.example_request.build_s3_client")
    def test_unknown_model_is_rejected_without_writing(self, build):
        client = build.return_value
        client.list_objects_v2.return_value = {"Contents": [{"Key": "repo/other/config.pbtxt"}]}
        with self.assertRaises(BadRequestError):
            save_deployment_example(self.request(example_model_name="model", example_request={}))
        client.put_object.assert_not_called()

    @patch("app.services.deployment.example_request.build_s3_client")
    def test_storage_write_failure_is_reported(self, build):
        client = build.return_value
        client.list_objects_v2.return_value = {"Contents": [{"Key": "repo/model/config.pbtxt"}]}
        client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        with self.assertRaises(BadGatewayError):
            save_deployment_example(self.request(example_model_name="model", example_request={}))

    def test_nonfinite_json_is_rejected_before_storage_access(self):
        with self.assertRaises(BadRequestError):
            save_deployment_example(self.request(example_model_name="model", example_request={"x": float("nan")}))
