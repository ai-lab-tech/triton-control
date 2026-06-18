"""Integration-style unit tests for instance CRUD, model operations, S3 API
endpoints, dashboard alerts, and access control.

All external dependencies (database sessions, service calls, Triton HTTP
requests) are replaced with mocks so the tests run without any live
infrastructure.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from botocore.exceptions import ClientError, SSLError
from fastapi import HTTPException

from app.exceptions import BadRequestError, ForbiddenError, UnauthorizedError

from app.api.instance_api import create_instance, delete_instance, update_instance
from app.api.model_api import get_instance_models, infer_instance_model, load_instance_model
from app.services.access import ensure_instance_access
from app.core.identity import require_user_entity
from app.services.triton.client import TritonService
from app.services.triton.config import extract_triton_error_detail
from app.api.s3_api import (
    delete_instance_s3_content,
    get_instance_s3_content,
    get_instance_s3_content_raw,
    list_instance_s3,
    put_instance_s3_content,
    update_instance_s3,
)
from app.services.storage import s3_client
from app.services.storage.s3 import require_s3_client as _build_s3_client
from app.api.dashboard_api import list_dashboard_alerts
from app.db.entities import DashboardAlertEntity, TritonInstanceEntity, UserEntity
from app.schemas import CreateTritonInstanceRequest, UpdateInstanceS3Request, UpdateTritonInstanceRequest


class _ExecResult:
    def __init__(self, first=None, all_rows=None):
        self._first = first
        self._all = list(all_rows or [])

    def first(self):
        return self._first

    def all(self):
        return self._all


class _FakeSession:
    def __init__(self, *, get_map=None, exec_results=None):
        self.get_map = get_map or {}
        self.exec_results = list(exec_results or [])
        self.added = []
        self.deleted = []
        self.commit_count = 0
        self.refresh_count = 0

    def get(self, _model, key):
        return self.get_map.get(key)

    def exec(self, _query):
        if self.exec_results:
            return self.exec_results.pop(0)
        return _ExecResult()

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        if hasattr(_obj, "id") and getattr(_obj, "id") is None:
            _obj.id = 1
        self.refresh_count += 1


class _S3Body:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload


class _S3Client:
    def __init__(self):
        self.put_calls = []
        self.delete_calls = []
        self.delete_objects_calls = []

    def list_objects_v2(self, **kwargs):
        if "Delimiter" not in kwargs:
            prefix = kwargs.get("Prefix", "")
            return {
                "Contents": [
                    {"Key": f"{prefix}"},
                    {"Key": f"{prefix}config.pbtxt"},
                    {"Key": f"{prefix}1/model.plan"},
                ],
                "IsTruncated": False,
            }
        return {
            "CommonPrefixes": [{"Prefix": "models/folder/"}],
            "Contents": [
                {"Key": "models/file.txt", "Size": 12, "LastModified": datetime.now(timezone.utc)},
                {"Key": "models/folder/"},
            ],
        }

    def get_object(self, **kwargs):
        if kwargs["Key"].endswith("missing.txt"):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _S3Body(b"hello")}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)

    def delete_objects(self, **kwargs):
        self.delete_objects_calls.append(kwargs)


class _BodyRequest:
    def __init__(self, payload: bytes, headers=None):
        self._payload = payload
        self.headers = headers or {}

    async def body(self):
        return self._payload


class ApiHelperTests(unittest.TestCase):
    def _instance(self):
        return TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            created_at=datetime.now(timezone.utc),
            s3_enabled=True,
            s3_endpoint="http://minio:9000",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
            s3_prefix="models/",
            s3_use_https=False,
            s3_verify_ssl=False,
            s3_address_style="path",
        )

    def test_BuildS3Client_ConfigInvalidOrValid_RaisesOrReturnsClient(self):
        # Arrange
        entity = self._instance()
        entity.s3_endpoint = None

        # Act / Assert
        with self.assertRaises(BadRequestError):
            _build_s3_client(entity)

    def test_MetricsUrl_BaseEndpointProvided_AppendsMetricsPath(self):
        # Act
        create_request = CreateTritonInstanceRequest(
            url="http://triton:8000",
            metrics_url="http://triton:8002",
        )
        update_request = UpdateTritonInstanceRequest(
            url="http://triton:8000",
            metrics_url="triton:8002/",
        )
        custom_request = UpdateTritonInstanceRequest(
            url="http://triton:8000",
            metrics_url="http://triton:8002/custom",
        )

        # Assert
        self.assertEqual(create_request.metrics_url, "http://triton:8002/metrics")
        self.assertEqual(update_request.metrics_url, "https://triton:8002/metrics")
        self.assertEqual(custom_request.metrics_url, "http://triton:8002/custom")

    def test_TritonEndpoint_WhitespaceProvided_StripsBeforeSaving(self):
        # Act
        create_request = CreateTritonInstanceRequest(
            url="  triton:8000  ",
            metrics_url="  triton:8002  ",
        )
        update_request = UpdateTritonInstanceRequest(
            url="  http://triton:8000/  ",
            metrics_url="  http://triton:8002/metrics/  ",
        )

        # Assert
        self.assertEqual(create_request.url, "https://triton:8000")
        self.assertEqual(create_request.metrics_url, "https://triton:8002/metrics")
        self.assertEqual(update_request.url, "http://triton:8000")
        self.assertEqual(update_request.metrics_url, "http://triton:8002/metrics")

        # Arrange
        entity = self._instance()
        entity.s3_secret_key_enc = None

        # Act / Assert
        with self.assertRaises(BadRequestError):
            _build_s3_client(entity)

        # Arrange
        entity = self._instance()

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch("app.services.storage.s3_client.boto3.client", return_value="client") as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        self.assertEqual(boto.call_args.kwargs["aws_secret_access_key"], "secret")

    def test_BuildS3Client_HttpsEndpointOverridesMissingHttpsFlag(self):
        # Arrange
        entity = self._instance()
        entity.s3_endpoint = "https://s3-nue1.datev.cloud:443"
        entity.s3_use_https = False

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client.boto3.client", return_value="client"
        ) as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        self.assertTrue(boto.call_args.kwargs["use_ssl"])

    def test_BuildS3Client_HttpEndpointOnPort443_NormalizesEndpointToHttps(self):
        # Arrange
        entity = self._instance()
        entity.s3_endpoint = "http://s3-nue1.datev.cloud:443"
        entity.s3_use_https = False

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client.boto3.client", return_value="client"
        ) as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        self.assertEqual(boto.call_args.kwargs["endpoint_url"], "https://s3-nue1.datev.cloud:443")
        self.assertTrue(boto.call_args.kwargs["use_ssl"])

    def test_BuildS3Client_LocalEndpointWithVirtualStyle_ForcesPathStyle(self):
        # Arrange
        entity = self._instance()
        entity.s3_endpoint = "https://host.minikube.internal:9000"
        entity.s3_address_style = "virtual"

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client.boto3.client", return_value="client"
        ) as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        self.assertEqual(boto.call_args.kwargs["config"].s3["addressing_style"], "path")

    def test_BuildS3Client_HttpsEndpointWithUnsetVerify_DefaultsToVerifyTrue(self):
        # Arrange
        entity = self._instance()
        entity.s3_endpoint = "https://s3-nue1.datev.cloud:443"
        entity.s3_use_https = True
        entity.s3_verify_ssl = None

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client.boto3.client", return_value="client"
        ) as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        self.assertTrue(boto.call_args.kwargs["verify"])

    def test_BuildS3Client_VerifySslWithStoredCaCertificate_PassesCaBundleToBoto(self):
        # Arrange
        entity = self._instance()
        entity.s3_use_https = True
        entity.s3_verify_ssl = True
        entity.s3_ca_certificate = "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----"

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client._write_ca_certificate_file", return_value="/tmp/s3-ca.pem"
        ) as write_ca, patch("app.services.storage.s3_client.boto3.client", return_value="client") as boto:
            client = _build_s3_client(entity)

        # Assert
        self.assertEqual(client, "client")
        write_ca.assert_called_once_with(entity.s3_ca_certificate)
        self.assertEqual(boto.call_args.kwargs["verify"], "/tmp/s3-ca.pem")

    def test_BuildCaBundle_CustomCertificateProvided_PreservesDefaultTrustBundle(self):
        # Arrange
        custom_certificate = "-----BEGIN CERTIFICATE-----\ncustom\n-----END CERTIFICATE-----"
        default_certificate = "-----BEGIN CERTIFICATE-----\ndefault\n-----END CERTIFICATE-----"
        test_case = self

        class FakePath:
            def __init__(self, _value):
                pass

            def is_file(self):
                return True

            def read_text(self, encoding):
                test_case.assertEqual(encoding, "utf-8")
                return default_certificate

        # Act
        with patch("app.services.storage.s3_client.Path", FakePath):
            bundle = s3_client._build_ca_bundle(custom_certificate)

        # Assert
        self.assertIn(default_certificate, bundle)
        self.assertIn(custom_certificate, bundle)
        self.assertTrue(bundle.endswith("\n"))

    def test_GetOrCreateUser_ClaimsProvided_ResolvesAndEnforcesAccess(self):
        # Arrange
        user = UserEntity(
            id=1,
            email="user@example.com",
            name="User",
            role="viewer",
            auth_provider="local",
            assigned_instances=["gpu-a"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )

        # Act
        session = _FakeSession(exec_results=[_ExecResult(first=user)])
        resolved = require_user_entity(session, {"email": "user@example.com"})

        # Assert
        self.assertEqual(resolved.email, "user@example.com")

        # Act / Assert
        session = _FakeSession(exec_results=[_ExecResult(first=None)])
        with self.assertRaises(UnauthorizedError):
            require_user_entity(session, {"email": "missing@example.com"})

        # Act / Assert
        ensure_instance_access(session, {"role": "admin"}, "gpu-a")
        with patch("app.services.access.require_user_entity", return_value=user):
            ensure_instance_access(_FakeSession(), {"email": "user@example.com"}, "gpu-a")

        with patch("app.services.access.require_user_entity", return_value=user):
            with self.assertRaises(ForbiddenError):
                ensure_instance_access(_FakeSession(), {"email": "user@example.com"}, "other")

    def test_UpdateInstanceS3_EnableOrDisableRequest_UpdatesS3Configuration(self):
        # Arrange
        instance = self._instance()
        session = _FakeSession(get_map={1: instance})
        claims = {"role": "admin"}

        request = UpdateInstanceS3Request(
            enabled=True,
            endpoint="https://new-minio",
            bucket="new-bucket",
            region="eu",
            prefix="new/",
            access_key="new-ak",
            secret_key="new-secret",
            use_https=False,
            verify_ssl=True,
            ca_certificate="cert",
            address_style="virtual",
        )

        # Act
        with patch("app.services.storage.s3.hash_secret", return_value="hashed"), patch("app.services.storage.s3.encrypt_secret", return_value="enc2"):
            dto = update_instance_s3(1, request, session=session, claims=claims)

        # Assert
        self.assertTrue(dto.enabled)
        self.assertTrue(dto.use_https)
        self.assertEqual(dto.region, "eu")
        self.assertEqual(dto.access_key, "new-ak")
        self.assertTrue(dto.secret_configured)
        self.assertEqual(dto.ca_certificate, "cert")
        self.assertEqual(instance.s3_secret_key_hash, "hashed")
        self.assertEqual(instance.s3_secret_key_enc, "enc2")

        # Arrange: a follow-up partial update should keep the stored CA when
        # SSL verification remains enabled and no replacement certificate is sent.
        request = UpdateInstanceS3Request(enabled=True)

        # Act
        dto = update_instance_s3(1, request, session=session, claims=claims)

        # Assert
        self.assertTrue(dto.verify_ssl)
        self.assertEqual(dto.ca_certificate, "cert")

        # Arrange: new S3 configuration with no region should store the default AWS region.
        instance = self._instance()
        instance.s3_region = None
        instance.s3_secret_key_enc = None
        session = _FakeSession(get_map={1: instance})
        request = UpdateInstanceS3Request(
            enabled=True,
            endpoint="https://new-minio",
            bucket="new-bucket",
            access_key="new-ak",
            secret_key="new-secret",
        )

        # Act
        with patch("app.services.storage.s3.hash_secret", return_value="hashed"), patch("app.services.storage.s3.encrypt_secret", return_value="enc2"):
            dto = update_instance_s3(1, request, session=session, claims=claims)

        # Assert
        self.assertEqual(dto.region, "us-east-1")

        # Act
        disable = UpdateInstanceS3Request(enabled=False)
        dto = update_instance_s3(1, disable, session=session, claims=claims)

        # Assert
        self.assertFalse(dto.enabled)
        self.assertIsNone(instance.s3_endpoint)

    def test_UpdateInstanceS3_EnableWithoutRequiredFields_RaisesBadRequest(self):
        # Arrange
        instance = self._instance()
        instance.s3_endpoint = None
        instance.s3_bucket = None
        instance.s3_access_key = None
        instance.s3_secret_key_enc = None
        session = _FakeSession(get_map={1: instance})
        request = UpdateInstanceS3Request(enabled=True)

        # Act / Assert
        with self.assertRaises(HTTPException) as exc:
            update_instance_s3(1, request, session=session, claims={"role": "admin"})
        self.assertEqual(exc.exception.status_code, 400)

    def test_InstanceS3Read_ListAndContentRequested_ReturnsEntriesAndContent(self):
        # Arrange
        instance = self._instance()
        session = _FakeSession(get_map={1: instance})
        client = _S3Client()

        # Act
        with patch("app.services.storage.s3.require_s3_client", return_value=client):
            listed = list_instance_s3(1, prefix="", session=session, claims={"role": "admin"})
            content = get_instance_s3_content(1, path="/file.txt", session=session, claims={"role": "admin"})
            raw = get_instance_s3_content_raw(1, path="/file.txt", session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual(listed.prefix, "/models")
        self.assertEqual(len(listed.entries), 2)
        self.assertEqual(content.content, "hello")
        self.assertEqual(raw.body, b"hello")

        # Act / Assert
        with patch("app.services.storage.s3.require_s3_client", return_value=client):
            with self.assertRaises(HTTPException):
                get_instance_s3_content(1, path="/missing.txt", session=session, claims={"role": "admin"})

    def test_InstanceS3Read_SslVerificationFailure_ReturnsBadGatewayDetail(self):
        # Arrange
        instance = self._instance()
        session = _FakeSession(get_map={1: instance})
        client = _S3Client()

        def _raise_ssl(**_kwargs):
            raise SSLError(
                endpoint_url=instance.s3_endpoint,
                error="certificate verify failed: unable to get local issuer certificate",
            )

        client.list_objects_v2 = _raise_ssl

        # Act / Assert
        with patch("app.services.storage.s3.require_s3_client", return_value=client):
            with self.assertRaises(HTTPException) as exc:
                list_instance_s3(1, prefix="", session=session, claims={"role": "admin"})
        self.assertEqual(exc.exception.status_code, 502)
        self.assertIn("unable to get local issuer certificate", exc.exception.detail)


class ApiAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def _instance_session(self):
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            health_live=True,
            health_ready=True,
            created_at=datetime.now(timezone.utc),
        )
        return _FakeSession(get_map={1: instance}), instance

    async def test_CreateInstance_ReadyTritonAndValidRequest_ReturnsCreatedDto(self):
        # Arrange
        session = _FakeSession(exec_results=[_ExecResult(first=None), _ExecResult(first=None)])
        user = UserEntity(
            id=11,
            email="u@example.com",
            name="User",
            role="admin",
            auth_provider="local",
            assigned_instances=[],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        request = CreateTritonInstanceRequest(
            url="triton:8000",
            name="gpu-a",
            verify_ssl=True,
            ca_certificate="cert",
        )
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {"name": "srv"},
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            collect_metrics_snapshot=AsyncMock(
                return_value={
                    "cpu": 0,
                    "ram": 0,
                    "gpu": 0,
                    "checked_at": None,
                    "error": None,
                },
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "m1", "version": "1", "state": "READY"}]),
        )

        # Act
        with patch(
            "app.services.triton.instances.TritonService",
            return_value=service,
        ) as triton_service, patch(
            "app.services.triton.instances.require_user_entity",
            return_value=user,
        ):
            dto = await create_instance(request, session=session, claims={"email": "u@example.com", "role": "member"})

        # Assert
        triton_service.assert_called_with("https://triton:8000", True, "cert", timeout=5.0)
        self.assertEqual(dto.name, "gpu-a")
        self.assertEqual(dto.model_names, ["m1"])
        self.assertEqual(dto.repository_models[0].name, "m1")
        self.assertTrue(dto.triton_verify_ssl)
        self.assertEqual(dto.triton_ca_certificate, "cert")

        # Act / Assert
        with self.assertRaises(HTTPException):
            await create_instance(request, session=session, claims={"email": "u@example.com", "role": "viewer"})

    async def test_CreateInstance_TritonNotReady_IncludesSnapshotErrorDetail(self):
        # Arrange
        session = _FakeSession(exec_results=[_ExecResult(first=None), _ExecResult(first=None)])
        request = CreateTritonInstanceRequest(url="https://triton:8000", verify_ssl=True)
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": False,
                    "ready": False,
                    "checked_at": datetime.now(timezone.utc),
                    "error": "ready request failed: certificate verify failed",
                }
            ),
        )

        # Act / Assert
        with patch("app.services.triton.instances.TritonService", return_value=service):
            with self.assertRaises(HTTPException) as exc:
                await create_instance(request, session=session, claims={"email": "u@example.com", "role": "member"})

        self.assertEqual(exc.exception.status_code, 503)
        self.assertIn("certificate verify failed", exc.exception.detail)

    async def test_UpdateInstance_ReadyTritonAndNewConnection_UpdatesEndpointAndSsl(self):
        # Arrange
        session, instance = await self._instance_session()
        session.exec_results = [_ExecResult(first=None)]
        request = UpdateTritonInstanceRequest(
            url="https://triton-new:8000",
            verify_ssl=True,
            ca_certificate="cert",
        )
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {"name": "srv", "version": "2.0"},
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                },
            ),
            collect_metrics_snapshot=AsyncMock(
                return_value={
                    "cpu": 0,
                    "ram": 0,
                    "gpu": 0,
                    "checked_at": None,
                    "error": None,
                },
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "m2", "version": "1", "state": "READY"}]),
        )

        # Act
        with patch(
            "app.services.triton.instances.TritonService",
            return_value=service,
        ) as triton_service:
            dto = await update_instance(1, request, session=session, claims={"role": "admin"})

        # Assert
        triton_service.assert_called_with("https://triton-new:8000", True, "cert", timeout=5.0)
        self.assertEqual(dto.url, "https://triton-new:8000")
        self.assertEqual(dto.model_names, ["m2"])
        self.assertEqual(dto.repository_models[0].name, "m2")
        self.assertTrue(dto.triton_verify_ssl)
        self.assertEqual(dto.triton_ca_certificate, "cert")
        self.assertEqual(instance.url, "https://triton-new:8000")

        # Act / Assert
        with self.assertRaises(HTTPException) as exc:
            await update_instance(1, request, session=session, claims={"role": "viewer"})
        self.assertEqual(exc.exception.status_code, 403)

    async def test_UpdateInstance_TritonNotReady_IncludesSnapshotErrorDetail(self):
        # Arrange
        session, _instance = await self._instance_session()
        session.exec_results = [_ExecResult(first=None)]
        request = UpdateTritonInstanceRequest(url="https://triton-new:8000", verify_ssl=True)
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": False,
                    "ready": False,
                    "checked_at": datetime.now(timezone.utc),
                    "error": "v2 request failed: unable to get local issuer certificate",
                }
            ),
        )

        # Act / Assert
        with patch("app.services.triton.instances.TritonService", return_value=service):
            with self.assertRaises(HTTPException) as exc:
                await update_instance(1, request, session=session, claims={"role": "admin"})

        self.assertEqual(exc.exception.status_code, 503)
        self.assertIn("unable to get local issuer certificate", exc.exception.detail)

    async def test_DeleteInstance_AdminDeletesAndMemberForbidden(self):
        # Arrange
        session, instance = await self._instance_session()
        user = UserEntity(
            id=12,
            email="u@example.com",
            name="User",
            role="member",
            auth_provider="local",
            assigned_instances=["gpu-a", "other"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.exec_results = [_ExecResult(all_rows=[user])]

        # Act
        with patch("app.services.triton.instances.deployment_k8s.delete_namespace") as delete_namespace:
            response = delete_instance(1, session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual(response.status_code, 204)
        delete_namespace.assert_not_called()
        self.assertEqual(session.deleted, [instance])
        self.assertEqual(user.assigned_instances, ["other"])

        # Act / Assert
        with self.assertRaises(HTTPException) as exc:
            delete_instance(1, session=session, claims={"role": "member"})
        self.assertEqual(exc.exception.status_code, 403)

    async def test_DeleteInstance_SelfDeployed_DeletesNamespaceBeforeDbRecord(self):
        # Arrange
        session, instance = await self._instance_session()
        instance.is_self_deployed = True
        instance.deployment_namespace = "triton-one"
        session.exec_results = [_ExecResult(all_rows=[])]

        # Act
        with patch(
            "app.services.triton.instances.deployment_k8s.delete_namespace",
            return_value="Namespace 'triton-one' deletion requested.",
        ) as delete_namespace:
            response = delete_instance(1, session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual(response.status_code, 204)
        delete_namespace.assert_called_once_with("triton-one")
        self.assertEqual(session.deleted, [instance])

    async def test_DeleteInstance_SelfDeployedNamespaceDeleteFails_DoesNotDeleteDbRecord(self):
        # Arrange
        session, instance = await self._instance_session()
        instance.is_self_deployed = True
        instance.deployment_namespace = "triton-one"
        session.exec_results = [_ExecResult(all_rows=[])]

        # Act / Assert
        with patch(
            "app.services.triton.instances.deployment_k8s.delete_namespace",
            side_effect=RuntimeError("kube down"),
        ):
            with self.assertRaises(HTTPException) as exc:
                delete_instance(1, session=session, claims={"role": "admin"})

        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(session.deleted, [])

    async def test_InstanceModelsAndInfer_ValidInstanceAndPayload_ReturnsModelsAndResponse(self):
        # Arrange
        session, instance = await self._instance_session()
        instance.repository_models = [
            {"name": "b", "version": "2", "state": "READY"},
            {"name": "a", "version": "1", "state": "READY", "reason": "ok"},
        ]
        service = SimpleNamespace(
            get_repository_index=AsyncMock(
                return_value=[
                    {"name": "b", "version": 2, "state": "READY"},
                    {"name": "a", "version": "1", "state": "READY", "reason": "ok"},
                    {"name": ""},
                    {"x": "invalid"},
                ]
            ),
            infer_model_raw=AsyncMock(
                return_value=SimpleNamespace(
                    content=b'{"ok":true}',
                    status_code=200,
                    headers={"content-type": "application/json"},
                )
            ),
            collect_inference_stats_snapshot=AsyncMock(
                side_effect=[
                    {
                        "series": {
                            "m|1": {
                                "model": "m",
                                "version": "1",
                                "request_count": 1,
                                "total_us": 1000,
                                "queue_us": 100,
                                "input_us": 100,
                                "infer_us": 700,
                                "output_us": 100,
                            }
                        },
                        "error": None,
                    },
                    {
                        "series": {
                            "m|1": {
                                "model": "m",
                                "version": "1",
                                "request_count": 2,
                                "total_us": 3000,
                                "queue_us": 300,
                                "input_us": 300,
                                "infer_us": 2200,
                                "output_us": 200,
                            }
                        },
                        "error": None,
                    },
                ],
            ),
            collect_inference_metrics_snapshot=AsyncMock(
                side_effect=[
                    {
                        "series": {
                            "m|1": {
                                "model": "m",
                                "version": "1",
                                "request_count": 1,
                                "total_us": 1000,
                                "queue_us": 100,
                                "input_us": 100,
                                "infer_us": 700,
                                "output_us": 100,
                            }
                        },
                        "error": None,
                    },
                    {
                        "series": {
                            "m|1": {
                                "model": "m",
                                "version": "1",
                                "request_count": 2,
                                "total_us": 3000,
                                "queue_us": 300,
                                "input_us": 300,
                                "infer_us": 2200,
                                "output_us": 200,
                            }
                        },
                        "error": None,
                    },
                ],
            ),
            inference_metrics_delta=TritonService.inference_metrics_delta,
        )

        # Act
        with patch("app.services.triton.models.TritonService") as triton_service, patch(
            "app.services.access.ensure_instance_access", return_value=None
        ):
            rows = await get_instance_models(1, session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual([r.name for r in rows], ["a", "b"])
        triton_service.assert_not_called()

        # Arrange / Act
        request = _BodyRequest(b'{"inputs":[]}', headers={"content-type": "application/json"})
        with patch("app.services.triton.models.TritonService", return_value=service), patch(
            "app.services.access.ensure_instance_access", return_value=None
        ):
            response = await infer_instance_model(
                1,
                "m",
                "1",
                {"inputs": []},
                request,
                session=session,
                claims={"role": "admin"},
            )

        # Assert
        self.assertEqual(response.status_code, 200)

        # Act / Assert
        with self.assertRaises(HTTPException) as exc:
            await load_instance_model(1, "m", session=session, claims={"role": "viewer"})
        self.assertEqual(exc.exception.status_code, 403)

        # Act / Assert
        empty_request = _BodyRequest(b"", headers={})
        with patch("app.services.triton.models.TritonService", return_value=service), patch(
            "app.services.access.ensure_instance_access", return_value=None
        ):
            with self.assertRaises(HTTPException):
                await infer_instance_model(
                    1,
                    "m",
                    "1",
                    {},
                    empty_request,
                    session=session,
                    claims={"role": "admin"},
                )

    async def test_InstanceModels_UnhealthyInstance_ReturnsEmptyWithoutCallingTriton(self):
        # Arrange
        session, instance = await self._instance_session()
        instance.health_live = False
        instance.health_ready = False

        # Act
        with patch("app.services.triton.models.TritonService") as triton_service:
            rows = await get_instance_models(1, session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual(rows, [])
        triton_service.assert_not_called()

    async def test_PutInstanceS3Content_ValidConfigPbtxt_WritesObjectAndReturnsMetadata(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            created_at=datetime.now(timezone.utc),
            s3_enabled=True,
            s3_endpoint="http://minio:9000",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
            s3_prefix="",
        )
        session = _FakeSession(get_map={1: instance})
        client = _S3Client()
        # Act
        with patch("app.services.storage.s3.require_s3_client", return_value=client), patch("app.services.storage.s3.validate_triton_config_pbtxt", return_value=None):
            result = put_instance_s3_content(
                1,
                path="config.pbtxt",
                content=b'name: "x"',
                content_type="text/plain",
                session=session,
                claims={"role": "admin"},
            )

        # Assert
        self.assertEqual(result.path, "/config.pbtxt")
        self.assertEqual(client.put_calls[0]["ContentType"], "text/plain")

        # Act / Assert
        with self.assertRaises(HTTPException) as exc:
            put_instance_s3_content(
                1,
                path="config.pbtxt",
                content=b'name: "x"',
                content_type="text/plain",
                session=session,
                claims={"role": "viewer"},
            )
        self.assertEqual(exc.exception.status_code, 403)

    async def test_PutInstanceS3Content_BinaryPayload_WritesRawBytes(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            created_at=datetime.now(timezone.utc),
            s3_enabled=True,
            s3_endpoint="http://minio:9000",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
            s3_prefix="",
        )
        session = _FakeSession(get_map={1: instance})
        client = _S3Client()
        # Act
        with patch("app.services.storage.s3.require_s3_client", return_value=client):
            result = put_instance_s3_content(
                1,
                path="weights.bin",
                content=b"\x00\xff\x01",
                content_type="application/octet-stream",
                session=session,
                claims={"role": "admin"},
            )

        # Assert
        self.assertEqual(result.path, "/weights.bin")
        self.assertEqual(result.size, 3)
        self.assertEqual(client.put_calls[0]["Body"], b"\x00\xff\x01")
        self.assertEqual(client.put_calls[0]["ContentType"], "application/octet-stream")

    async def test_DeleteInstanceS3Content_FileOrFolderPath_DeletesExpectedObjects(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            created_at=datetime.now(timezone.utc),
            s3_enabled=True,
            s3_endpoint="http://minio:9000",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
            s3_prefix="models/",
        )
        session = _FakeSession(get_map={1: instance})
        client = _S3Client()

        # Act
        with patch("app.services.storage.s3.require_s3_client", return_value=client):
            file_result = delete_instance_s3_content(
                1,
                path="resnet/config.pbtxt",
                session=session,
                claims={"role": "admin"},
            )
            folder_result = delete_instance_s3_content(
                1,
                path="resnet/",
                session=session,
                claims={"role": "admin"},
            )

        # Assert
        self.assertEqual(file_result.deleted, 1)
        self.assertEqual(client.delete_calls[0]["Key"], "models/resnet/config.pbtxt")
        self.assertEqual(folder_result.deleted, 3)
        self.assertEqual(client.delete_objects_calls[0]["Delete"]["Objects"][0]["Key"], "models/resnet/")


class DashboardApiTests(unittest.TestCase):
    def test_ListDashboardAlerts_AdminOrViewerClaims_ReturnsScopedAlerts(self):
        # Arrange
        alert = DashboardAlertEntity(
            id=1,
            instance_id=2,
            instance_name="gpu-a",
            icon="warning",
            label="l",
            tone="danger",
            created_at=datetime.now(timezone.utc),
        )
        session = _FakeSession(exec_results=[_ExecResult(all_rows=[alert])])

        # Act
        rows = list_dashboard_alerts(session=session, claims={"role": "admin"})

        # Assert
        self.assertEqual(len(rows), 1)

        # Arrange
        user = UserEntity(
            id=1,
            email="u@example.com",
            name="U",
            role="viewer",
            auth_provider="local",
            assigned_instances=[],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session = _FakeSession(exec_results=[_ExecResult(all_rows=[])])

        # Act
        with patch("app.services.dashboard.require_user_entity", return_value=user):
            rows = list_dashboard_alerts(session=session, claims={"role": "viewer"})

        # Assert
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
