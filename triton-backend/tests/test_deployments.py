"""Unit tests for Kubernetes deployment API and service behavior."""

import unittest
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import ANY, patch

from fastapi import HTTPException
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.api.deployment_api import create_deployment as create_deployment_api
from app.api.deployment_api import delete_deployment as delete_deployment_api
from app.exceptions import BadRequestError, ConflictError
from app.schemas import CreateDeploymentRequest
from app.services.deployment import deployment as deployments
from app.services.deployment import kubernetes as k8s
from app.services.deployment import records


class _FakeSession:
    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        return False


class DeploymentServiceTests(unittest.TestCase):
    def _request(self) -> CreateDeploymentRequest:
        return CreateDeploymentRequest(
            deployment_name="triton-minio",
            image="custom/triton:dev",
            s3_url="s3://http://minio:9000/triton-models",
            s3_access_key="minioadmin",
            s3_secret_key="super-secret-value",
        )

    def test_CreateDeployment_AdminClaim_AppliesNamespaceAndK8sObjects(self) -> None:
        # Arrange
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []

        # Act
        with patch(
            "app.services.deployment.deployment.upsert_deployed_instance",
            return_value=SimpleNamespace(id=7),
        ) as upsert:
            response = deployments.create_deployment(
                self._request(),
                SimpleNamespace(),
                {"role": "admin"},
                lambda fn, *args: scheduled.append((fn, args)),
            )

        # Assert
        upsert.assert_called_once()
        self.assertEqual(response.instance_id, 7)
        self.assertEqual(
            response.applied_resources,
            [
                "Secret/triton-minio-s3-credentials",
                "Deployment/triton-minio",
                "Service/triton-minio-service",
            ],
        )
        self.assertEqual(response.namespace, "triton-minio")
        self.assertEqual(response.secret_name, "triton-minio-s3-credentials")
        self.assertEqual(response.image, "custom/triton:dev")
        self.assertNotIn("super-secret-value", str(response))
        self.assertEqual(
            upsert.call_args.kwargs["triton_url"],
            "http://triton-minio-service.triton-minio.svc.cluster.local:18000",
        )
        self.assertEqual(
            upsert.call_args.kwargs["metrics_url"],
            "http://triton-minio-service.triton-minio.svc.cluster.local:18002/metrics",
        )
        self.assertFalse(upsert.call_args.kwargs["initial_snapshot"]["health_live"])
        self.assertFalse(upsert.call_args.kwargs["initial_snapshot"]["health_ready"])
        self.assertIn("Deployment is starting", upsert.call_args.kwargs["initial_snapshot"]["health_error"])
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], k8s.apply_deployment_resources)

    def test_CreateDeploymentRequest_DeploymentName_NormalizesKubernetesName(self) -> None:
        # Act
        request = CreateDeploymentRequest(
            deployment_name=" Triton Minio!! ",
            image=" custom/image:latest ",
            s3_url="s3://bucket",
            s3_access_key="ak",
            s3_secret_key="secret",
        )

        # Assert
        self.assertEqual(request.deployment_name, "triton-minio")
        self.assertEqual(request.image, "custom/image:latest")

    def test_CreateDeploymentRequest_InvalidRequirements_RaisesValidationError(self) -> None:
        # Act / Assert
        for requirements_txt in ("numpy=>1", "bad package name!"):
            with self.subTest(requirements_txt=requirements_txt):
                with self.assertRaises(ValueError) as raised:
                    CreateDeploymentRequest(
                        deployment_name="triton",
                        image="custom/image:latest",
                        s3_url="s3://bucket",
                        s3_access_key="ak",
                        s3_secret_key="secret",
                        requirements_txt=requirements_txt,
                    )

                self.assertIn("Line 1:", str(raised.exception))

    def test_CreateDeploymentRequest_PackagingArbitraryEqualityRequirement_IsAccepted(self) -> None:
        # Act
        request = CreateDeploymentRequest(
            deployment_name="triton",
            image="custom/image:latest",
            s3_url="s3://bucket",
            s3_access_key="ak",
            s3_secret_key="secret",
            requirements_txt="numpy===custom-build",
        )

        # Assert
        self.assertEqual(request.requirements_txt, "numpy===custom-build")

    def test_CreateDeploymentRequest_S3Url_HttpsWithoutPrefix_NormalizesToS3Scheme(self) -> None:
        # Act
        request = CreateDeploymentRequest(
            deployment_name="triton",
            image="custom/image:latest",
            s3_url="https://minio:9000/triton-models",
            s3_access_key="ak",
            s3_secret_key="secret",
        )

        # Assert
        self.assertEqual(request.s3_url, "s3://https://minio:9000/triton-models")

    def test_CreateDeploymentRequest_HttpsS3WithoutCaCertificate_IsAccepted(self) -> None:
        request = CreateDeploymentRequest(
            deployment_name="triton",
            image="custom/image:latest",
            s3_url="https://object-store.example.com/triton-models",
            s3_access_key="ak",
            s3_secret_key="secret",
        )
        self.assertEqual(request.s3_url, "s3://https://object-store.example.com/triton-models")
        self.assertIsNone(request.s3_ca_certificate)

    def test_CreateDeploymentRequest_IngressHttpUrl_NormalizesHostAndScheme(self) -> None:
        # Act
        request = CreateDeploymentRequest(
            deployment_name="triton",
            image="custom/image:latest",
            s3_url="s3://bucket",
            s3_access_key="ak",
            s3_secret_key="secret",
            ingress_host="http://triton.example.local/",
        )

        # Assert
        self.assertEqual(request.ingress_host, "triton.example.local")
        self.assertEqual(request.ingress_scheme, "http")

    def test_ParseS3Url_HttpsEndpointOnPort443_SetsHttpsFlag(self) -> None:
        # Act
        parsed = records._parse_s3_url("s3://https://s3-nue1.datev.cloud:443/tax-value-extraction")

        # Assert
        self.assertEqual(parsed["endpoint"], "https://s3-nue1.datev.cloud:443")
        self.assertEqual(parsed["bucket"], "tax-value-extraction")
        self.assertTrue(parsed["use_https"])

    def test_ParseS3Url_HttpEndpointOnPort443_NormalizesToHttps(self) -> None:
        # Act
        parsed = records._parse_s3_url("s3://http://s3-nue1.datev.cloud:443/tax-value-extraction")

        # Assert
        self.assertEqual(parsed["endpoint"], "https://s3-nue1.datev.cloud:443")
        self.assertEqual(parsed["bucket"], "tax-value-extraction")
        self.assertTrue(parsed["use_https"])

    def test_UpsertDeployedInstance_HttpsS3WithCaCertificate_PersistsS3CaForControlPlane(self) -> None:
        # Arrange
        certificate = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
        request = self._request().model_copy(
            update={
                "s3_url": "s3://https://object-store.example.com:443/triton-models/prefix",
                "s3_ca_certificate": certificate,
            },
        )
        user = SimpleNamespace(id=11, assigned_instances=[])
        created: dict[str, Any] = {}

        # Act
        with patch("app.services.deployment.records.require_user_entity", return_value=user), patch(
            "app.services.deployment.records.instances.find_by_name", return_value=None
        ), patch("app.services.deployment.records.instances.find_by_url", return_value=None), patch(
            "app.services.deployment.records.instances.create",
            side_effect=lambda _session, **kwargs: created.update(kwargs) or SimpleNamespace(id=7, **kwargs),
        ), patch("app.services.deployment.records.users.save"):
            records.upsert_deployed_instance(
                request,
                SimpleNamespace(),
                {"role": "admin"},
                namespace="test-s3-ssl",
                deployment_name="test-s3-ssl",
                service_name="test-s3-ssl-service",
                secret_name="test-s3-ssl-s3-credentials",
                image="triton-image",
                triton_url="http://test-s3-ssl-service.triton-control.svc.cluster.local:18000",
                metrics_url="http://test-s3-ssl-service.triton-control.svc.cluster.local:18002/metrics",
                initial_snapshot=k8s.pending_snapshot(),
            )

        # Assert
        self.assertTrue(created["s3_verify_ssl"])
        self.assertEqual(created["s3_ca_certificate"], certificate)
        self.assertEqual(created["s3_endpoint"], "https://object-store.example.com:443")
        self.assertEqual(created["s3_bucket"], "triton-models")
        self.assertEqual(created["s3_prefix"], "prefix")
        self.assertEqual(created["s3_address_style"], "path")

    def test_UpsertDeployedInstance_DuplicateDeploymentName_RaisesConflict(self) -> None:
        # Arrange
        request = self._request()
        user = SimpleNamespace(id=11, assigned_instances=[])

        # Act / Assert
        with patch("app.services.deployment.records.require_user_entity", return_value=user), patch(
            "app.services.deployment.records.instances.find_by_name",
            return_value=SimpleNamespace(id=3, name="triton-minio"),
        ), patch("app.services.deployment.records.instances.find_by_url", return_value=None), patch(
            "app.services.deployment.records.instances.create"
        ) as create_instance:
            with self.assertRaises(ConflictError) as raised:
                records.upsert_deployed_instance(
                    request,
                    SimpleNamespace(),
                    {"role": "admin"},
                    namespace="triton-minio",
                    deployment_name="triton-minio",
                    service_name="triton-minio-service",
                    secret_name="triton-minio-s3-credentials",
                    image="triton-image",
                    triton_url="http://triton-minio-service.triton-minio.svc.cluster.local:18000",
                    metrics_url=None,
                    initial_snapshot=k8s.pending_snapshot(),
                )

        self.assertIn("Deployment name 'triton-minio' already exists", raised.exception.detail)
        create_instance.assert_not_called()

    def test_UpsertDeployedInstance_HttpsS3WithoutCaCertificate_StoresEmptyS3Ca(self) -> None:
        # Arrange
        request = self._request().model_copy(
            update={"s3_url": "s3://https://s3.amazonaws.com/triton-models"}
        )
        user = SimpleNamespace(id=11, assigned_instances=[])
        created: dict[str, Any] = {}

        # Act
        with patch("app.services.deployment.records.require_user_entity", return_value=user), patch(
            "app.services.deployment.records.instances.find_by_name", return_value=None
        ), patch("app.services.deployment.records.instances.find_by_url", return_value=None), patch(
            "app.services.deployment.records.instances.create",
            side_effect=lambda _session, **kwargs: created.update(kwargs) or SimpleNamespace(id=7, **kwargs),
        ), patch("app.services.deployment.records.users.save"):
            records.upsert_deployed_instance(
                request,
                SimpleNamespace(),
                {"role": "admin"},
                namespace="aws-s3",
                deployment_name="aws-s3",
                service_name="aws-s3-service",
                secret_name="aws-s3-s3-credentials",
                image="triton-image",
                triton_url="http://aws-s3-service.triton-control.svc.cluster.local:18000",
                metrics_url=None,
                initial_snapshot=k8s.pending_snapshot(),
            )

        # Assert
        self.assertTrue(created["s3_verify_ssl"])
        self.assertEqual(created["s3_ca_certificate"], "")
        self.assertEqual(created["s3_endpoint"], "https://s3.amazonaws.com")

    def test_ApplyDeploymentResources_AppliesK8sObjectsAndUpdatesInstance(self) -> None:
        # Arrange
        applied: list[dict[str, Any]] = []

        # Act
        with patch("app.services.deployment.kubernetes._client", return_value=object()), patch(
            "app.services.deployment.kubernetes._ensure_namespace"
        ) as ensure_namespace, patch(
            "kubernetes.utils.create_from_dict",
            side_effect=lambda _api_client, data, **_kwargs: applied.append(data),
        ), patch(
            "app.services.deployment.kubernetes._deployment_urls",
            return_value={"http": "http://lb:8000", "metrics": "http://lb:8002/metrics"},
        ), patch(
            "app.services.deployment.kubernetes.record_deployment_failure"
        ), patch(
            "app.services.deployment.kubernetes.update_instance_after_apply"
        ) as update_instance:
            k8s.apply_deployment_resources(
                7,
                self._request(),
                "triton-minio",
                "triton-minio",
                "triton-minio-service",
                "triton-minio-s3-credentials",
                "triton-image",
            )

        # Assert
        ensure_namespace.assert_called_once()
        self.assertEqual(applied[0]["kind"], "Secret")
        self.assertEqual(applied[0]["metadata"]["name"], "triton-minio-s3-credentials")
        self.assertEqual(applied[1]["kind"], "Deployment")
        self.assertEqual(applied[2]["spec"]["type"], "ClusterIP")
        self.assertEqual(len(applied), 3)
        container = applied[1]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], "triton-image")
        self.assertEqual(container["command"], ["/bin/bash", "-c"])
        start_script = container["args"][0]
        self.assertIn("exec tritonserver", start_script)
        self.assertIn("--model-repository=s3://http://minio:9000/triton-models", start_script)
        self.assertNotIn(
            "s3-model-sync",
            [
                container["name"]
                for container in applied[1]["spec"]["template"]["spec"].get("initContainers", [])
            ],
        )
        self.assertIn("--model-control-mode=explicit", start_script)
        self.assertIn("--allow-metrics=true", start_script)
        self.assertIn("'--load-model=*'", start_script)
        update_instance.assert_called_once_with(
            7,
            triton_url="http://lb:8000",
            metrics_url="http://lb:8002/metrics",
            applied_resources=[
                "Secret/triton-minio-s3-credentials",
                "Deployment/triton-minio",
                "Service/triton-minio-service",
            ],
        )

    def test_Manifests_StartupModelProvided_AddsLoadModelArgument(self) -> None:
        # Arrange
        request = self._request().model_copy(
            update={"model_name": "simple_identity", "allow_metrics": False},
        )

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        start_script = manifests[1]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("--model-control-mode=explicit", start_script)
        self.assertIn("--allow-metrics=false", start_script)
        self.assertIn("--load-model=simple_identity", start_script)

    def test_Manifests_VllmInitSync_UsesStableLocalRepository(self) -> None:
        request = self._request().model_copy(update={"repository_sync_mode": "init"})

        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        pod_spec = manifests[1]["spec"]["template"]["spec"]
        self.assertIn("--model-repository=/models", pod_spec["containers"][0]["args"][0])
        sync = pod_spec["initContainers"][-1]
        self.assertEqual(sync["name"], "s3-model-sync")
        self.assertIn("aws s3 sync", sync["args"][0])
        sync_env = {item["name"]: item.get("value") for item in sync["env"]}
        self.assertEqual(sync_env["S3_SOURCE"], "s3://triton-models")
        self.assertIn("REPOSITORY_NEXT=/tmp/.s3-sync-next", sync["args"][0])
        self.assertIn('mkdir -p "$REPOSITORY_NEXT/$model_name"', sync["args"][0])
        self.assertIn('cp -R "$source_dir/." "$REPOSITORY_NEXT/$model_name/"', sync["args"][0])
        self.assertIn('cp -R "$REPOSITORY_NEXT/." /models/', sync["args"][0])
        self.assertNotIn("cp -au", sync["args"][0])
        self.assertIn("model.json", sync["args"][0])
        self.assertIn("final_dir=/models${dir#/tmp/.s3-sync-next}", sync["args"][0])
        self.assertIn('escaped_dir=$(printf "%s\\n" "$final_dir" | sed "s/[\\\\&#]/\\\\\\\\&/g")', sync["args"][0])
        self.assertIn("${escaped_dir}/", sync["args"][0])

    def test_Manifests_IngressHostProvided_AddsHostRule(self) -> None:
        # Arrange
        request = self._request().model_copy(update={"ingress_host": "triton.example.local"})

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )
        urls = k8s._deployment_urls(request, object(), "triton-minio", "triton-minio-service")

        # Assert
        ingress = manifests[3]
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "triton.example.local")
        self.assertEqual(urls["http"], "https://triton.example.local")
        self.assertEqual(urls["metrics"], "https://triton.example.local/metrics")

    def test_Manifests_IngressHttpUrlProvided_UsesHostRuleAndHttpUrls(self) -> None:
        # Arrange
        request = CreateDeploymentRequest(
            deployment_name="triton-minio",
            image="custom/triton:dev",
            s3_url="s3://http://minio:9000/triton-models",
            s3_access_key="minioadmin",
            s3_secret_key="super-secret-value",
            ingress_host="http://triton.example.local",
        )

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )
        urls = k8s._deployment_urls(request, object(), "triton-minio", "triton-minio-service")

        # Assert
        ingress = manifests[3]
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "triton.example.local")
        self.assertEqual(urls["http"], "http://triton.example.local")
        self.assertEqual(urls["metrics"], "http://triton.example.local/metrics")

    def test_Manifests_DockerConfigProvided_AddsImagePullSecret(self) -> None:
        # Arrange
        dockerconfigjson = '{"auths":{"registry.example":{"auth":"token"}}}'
        request = self._request().model_copy(update={"dockerconfigjson": dockerconfigjson})

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        image_secret = manifests[0]
        deployment = manifests[2]
        self.assertEqual(image_secret["metadata"]["name"], "triton-minio-pull-secret")
        self.assertEqual(image_secret["type"], "kubernetes.io/dockerconfigjson")
        self.assertEqual(image_secret["stringData"][".dockerconfigjson"], dockerconfigjson)
        self.assertEqual(
            deployment["spec"]["template"]["spec"]["imagePullSecrets"],
            [{"name": "triton-minio-pull-secret"}],
        )

    def test_Manifests_DockerConfigProvided_TruncatesImagePullSecretName(self) -> None:
        # Arrange
        dockerconfigjson = '{"auths":{"registry.example":{"auth":"token"}}}'
        long_name = "triton-" + ("very-long-" * 8)
        request = self._request().model_copy(update={"dockerconfigjson": dockerconfigjson})

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            long_name,
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        image_secret = manifests[0]
        secret_name = image_secret["metadata"]["name"]
        self.assertLessEqual(len(secret_name), 63)
        self.assertTrue(secret_name.endswith("-pull-secret"))
        self.assertEqual(
            manifests[2]["spec"]["template"]["spec"]["imagePullSecrets"],
            [{"name": secret_name}],
        )

    def test_Manifests_PollMode_AddsRepositoryPollSeconds(self) -> None:
        # Arrange
        request = self._request().model_copy(
            update={
                "model_control_mode": "poll",
                "repository_sync_mode": "sidecar",
                "repository_poll_secs": 9,
                "model_name": "ignored",
            },
        )

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        start_script = manifests[1]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("--model-control-mode=poll", start_script)
        self.assertIn("--repository-poll-secs=9", start_script)
        self.assertNotIn("--load-model=", start_script)

    def test_CreateDeploymentRequest_InitSyncWithPollMode_IsRejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "init repository sync supports only explicit"):
            CreateDeploymentRequest(
                deployment_name="triton-minio",
                image="custom/triton:dev",
                s3_url="s3://bucket",
                s3_access_key="access",
                s3_secret_key="secret",
                repository_sync_mode="init",
                model_control_mode="poll",
            )

    def test_Manifests_SidecarSync_WaitsForFirstSyncAndPollsS3(self) -> None:
        request = self._request().model_copy(
            update={
                "repository_sync_mode": "sidecar",
                "repository_poll_secs": 9,
                "model_control_mode": "explicit",
            }
        )

        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        pod_spec = manifests[1]["spec"]["template"]["spec"]
        self.assertEqual(
            [container["name"] for container in pod_spec["containers"]],
            ["triton", "s3-model-sync"],
        )
        self.assertIn(".s3-sync-ready", pod_spec["containers"][0]["args"][0])
        sync_script = pod_spec["containers"][1]["args"][0]
        self.assertIn("while sleep 9", sync_script)
        self.assertIn("--delete", sync_script)
        self.assertIn("/models/", sync_script)
        self.assertIn('diff -qr "$REPOSITORY_CURRENT" "$REPOSITORY_NEXT"', sync_script)
        self.assertIn("S3 model repository unchanged", sync_script)
        self.assertNotIn("/models/.s3-sync-next", sync_script)
        self.assertIn("--model-control-mode=explicit", pod_spec["containers"][0]["args"][0])

    def test_Manifests_TritonContainer_SetsUserAndTorchCacheEnvironment(self) -> None:
        request = self._request().model_copy(update={"repository_sync_mode": "sidecar"})

        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        triton_container = manifests[1]["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item.get("value") for item in triton_container["env"]}
        self.assertEqual(env["LOGNAME"], "triton")
        self.assertEqual(env["USER"], "triton")
        self.assertEqual(env["HOME"], "/tmp")
        self.assertEqual(env["XDG_CACHE_HOME"], "/tmp/.cache")
        self.assertEqual(env["TORCHINDUCTOR_CACHE_DIR"], "/tmp/torchinductor")

    def test_AwsS3Source_CustomEndpoint_SeparatesEndpointAndBucketPath(self) -> None:
        self.assertEqual(
            k8s._aws_s3_source("s3://https://minio.example:9443/bucket/prefix"),
            ("s3://bucket/prefix", "https://minio.example:9443"),
        )

    def test_RepositorySyncImage_ChartEnvironment_IsDefaultAndRequestCanOverride(self) -> None:
        request = self._request()
        with patch.dict(
            "os.environ",
            {"TRITON_DEPLOY_S3_SYNC_IMAGE": "registry.example/s3-sync:prod"},
        ):
            self.assertEqual(k8s._repository_sync_image(request), "registry.example/s3-sync:prod")
            overridden = request.model_copy(
                update={"repository_sync_image": "registry.example/s3-sync:request"}
            )
            self.assertEqual(
                k8s._repository_sync_image(overridden),
                "registry.example/s3-sync:request",
            )

    def test_Manifests_RequirementsProvided_InstallsPackagesBeforeTritonStart(self) -> None:
        # Arrange
        request = self._request().model_copy(
            update={"model_name": "simple_identity", "requirements_txt": "numpy\npandas>=2 # data frames"},
        )

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        container = manifests[1]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["command"], ["/bin/bash", "-c"])
        self.assertEqual(len(container["args"]), 1)
        start_script = container["args"][0]
        self.assertIn(
            "python3 -m pip install --no-cache-dir --target /tmp/triton-python-packages numpy 'pandas>=2'",
            start_script,
        )
        self.assertIn("export PYTHONPATH=/tmp/triton-python-packages:${PYTHONPATH:-}", start_script)
        self.assertNotIn("/.local", start_script)
        self.assertIn("exec tritonserver", start_script)
        self.assertIn("--model-repository=s3://http://minio:9000/triton-models", start_script)
        self.assertIn("--load-model=simple_identity", start_script)

    def test_Manifests_S3CaCertificateProvided_AppendsCertificateToSystemBundlePath(self) -> None:
        # Arrange
        request = self._request().model_copy(
            update={
                "s3_url": "s3://https://object-store.example.com/triton-models",
                "s3_ca_certificate": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
            },
        )

        # Act
        manifests = k8s._manifests(
            request,
            "triton-minio",
            "triton-minio",
            "triton-minio-service",
            "triton-minio-s3-credentials",
            "triton-image",
        )

        # Assert
        secret = manifests[0]
        self.assertEqual(secret["kind"], "Secret")
        self.assertIn("S3_CA_CERTIFICATE", secret["stringData"])
        pod_spec = manifests[1]["spec"]["template"]["spec"]
        init_container = pod_spec["initContainers"][0]
        self.assertEqual(init_container["name"], "build-s3-ca-bundle")
        self.assertEqual(init_container["image"], "triton-image")
        self.assertEqual(
            init_container["securityContext"],
            {
                "runAsNonRoot": True,
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
        )
        init_script = init_container["args"][0]
        self.assertIn("cp /etc/ssl/certs/ca-certificates.crt /ca-bundle/ca-certificates.crt", init_script)
        self.assertIn("cat /s3-ca/s3-ca.crt >> /ca-bundle/ca-certificates.crt", init_script)
        container = pod_spec["containers"][0]
        self.assertIn(
            {
                "name": "AWS_CA_BUNDLE",
                "value": "/etc/ssl/certs/ca-certificates.crt",
            },
            container["env"],
        )
        self.assertNotIn("SSL_CERT_FILE", {item["name"] for item in container["env"]})
        start_script = container["args"][0]
        self.assertNotIn("cat /etc/triton/s3-ca/s3-ca.crt", start_script)
        volume_mounts = container["volumeMounts"]
        self.assertIn(
            {
                "name": "s3-ca-bundle",
                "mountPath": "/etc/ssl/certs/ca-certificates.crt",
                "subPath": "ca-certificates.crt",
                "readOnly": True,
            },
            volume_mounts,
        )
        self.assertIn("volumes", pod_spec)
        volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
        self.assertIn("s3-ca-bundle", volumes)
        self.assertEqual(volumes["s3-ca-bundle"], {"name": "s3-ca-bundle", "emptyDir": {}})
        self.assertIn("s3-ca-cert", volumes)
        self.assertEqual(volumes["s3-ca-cert"]["secret"]["secretName"], "triton-minio-s3-credentials")

    def test_CreateDeployment_ViewerClaim_SchedulesDeployment(self) -> None:
        # Arrange
        scheduled: list[tuple[Any, tuple[Any, ...]]] = []

        # Act
        with patch(
            "app.services.deployment.deployment.upsert_deployed_instance",
            return_value=SimpleNamespace(id=7),
        ):
            response = deployments.create_deployment(
                self._request(),
                SimpleNamespace(),
                {"role": "viewer"},
                lambda fn, *args: scheduled.append((fn, args)),
            )

        # Assert
        self.assertEqual(response.instance_id, 7)
        self.assertEqual(len(scheduled), 1)

    def test_ApplyDeploymentResources_KubernetesApiError_RecordsFailure(self) -> None:
        # Arrange
        exc = ApiException(status=500, reason="cluster unavailable")

        # Act
        with patch("app.services.deployment.kubernetes._client", return_value=object()), patch(
            "app.services.deployment.kubernetes._ensure_namespace",
            side_effect=exc,
        ), patch("app.services.deployment.kubernetes.record_deployment_failure") as record_failure:
            k8s.apply_deployment_resources(
                7,
                self._request(),
                "triton-minio",
                "triton-minio",
                "triton-minio-service",
                "triton-minio-s3-credentials",
                "triton-image",
            )

        # Assert
        self.assertIn("Kubernetes API error 500", record_failure.call_args.args[1])

    def test_CreateDeploymentApi_ServiceRaisesDomainError_ReturnsHttpError(self) -> None:
        # Arrange
        request = self._request()

        # Act / Assert
        with patch(
            "app.services.deployment.deployment.create_deployment",
            side_effect=BadRequestError("bad deployment request"),
        ):
            with self.assertRaises(HTTPException) as raised:
                create_deployment_api(
                    request,
                    background_tasks=SimpleNamespace(add_task=lambda *_args: None),
                    session=SimpleNamespace(),
                    claims={"role": "viewer"},
                )
        self.assertEqual(raised.exception.status_code, 400)

    def test_CreateDeploymentApi_ServiceRaisesConflict_ReturnsConflictHttpError(self) -> None:
        # Arrange
        request = self._request()

        # Act / Assert
        with patch(
            "app.services.deployment.deployment.create_deployment",
            side_effect=ConflictError("Deployment name 'triton-minio' already exists"),
        ):
            with self.assertRaises(HTTPException) as raised:
                create_deployment_api(
                    request,
                    background_tasks=SimpleNamespace(add_task=lambda *_args: None),
                    session=SimpleNamespace(),
                    claims={"role": "viewer"},
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("already exists", str(raised.exception.detail))

    def test_DeleteDeploymentApi_ServiceReturnsResult_ReturnsDeleteResponse(self) -> None:
        # Act
        with patch(
            "app.services.deployment.deployment.delete_deployment_instance",
            return_value={
                "status": "deleted",
                "message": "Namespace deletion requested.",
                "namespace": "triton",
            },
        ):
            response = delete_deployment_api(2, session=SimpleNamespace(), claims={"role": "admin"})

        # Assert
        self.assertEqual(response["status"], "deleted")

    def test_DeleteDeploymentInstance_NamespaceAlreadyDeleted_CleansDatabaseRows(self) -> None:
        # Arrange
        instance = SimpleNamespace(
            id=2,
            name="triton-minio",
            is_self_deployed=True,
            deployment_namespace="triton-minio",
        )
        user = SimpleNamespace(assigned_instances=["triton-minio", "other"])
        deleted_instances = []

        # Act
        with patch("app.services.deployment.deployment.get_instance_or_404", return_value=instance), patch(
            "app.services.deployment.kubernetes.delete_namespace",
            return_value="Namespace 'triton-minio' was already deleted.",
        ), patch("app.services.deployment.records.dashboard_alerts.delete_for_instance") as delete_alerts, patch(
            "app.services.deployment.records.perf_analyzer_repo.delete_runs_for_instance"
        ) as delete_perf_runs, patch(
            "app.services.deployment.records.users.list_all", return_value=[user]
        ), patch(
            "app.services.deployment.records.users.save"
        ) as save_user, patch(
            "app.services.deployment.records.instances.delete",
            side_effect=lambda _session, row: deleted_instances.append(row),
        ):
            response = deployments.delete_deployment_instance(SimpleNamespace(), {"role": "viewer"}, 2)

        # Assert
        delete_alerts.assert_called_once_with(ANY, 2, "triton-minio")
        delete_perf_runs.assert_called_once_with(ANY, 2)
        save_user.assert_called_once()
        self.assertEqual(user.assigned_instances, ["other"])
        self.assertEqual(deleted_instances, [instance])
        self.assertEqual(response.status, "deleted")

    def test_ReadDeploymentLogs_FailedPodDoesNotHideRunningPodLogs(self) -> None:
        # Arrange
        failed_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="opt125m-failed"),
            status=SimpleNamespace(
                phase="Failed",
                reason="UnexpectedAdmissionError",
                message="Pod was rejected: no healthy GPU devices present",
            ),
            spec=SimpleNamespace(containers=[SimpleNamespace(name="triton")]),
        )
        running_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="opt125m-running"),
            status=SimpleNamespace(phase="Running"),
            spec=SimpleNamespace(containers=[SimpleNamespace(name="triton")]),
        )
        core_api = SimpleNamespace(
            list_namespaced_pod=lambda namespace, label_selector: SimpleNamespace(
                items=[failed_pod, running_pod],
            ),
            read_namespaced_pod_log=lambda name, namespace, container, tail_lines, previous=False: (
                "running triton log" if not previous else ""
            ),
        )

        # Act
        with patch("app.services.deployment.kubernetes._client", return_value=object()), patch(
            "kubernetes.client.CoreV1Api",
            return_value=core_api,
        ):
            logs = k8s.read_deployment_logs("opt125m", "opt125m")

        # Assert
        self.assertIn("UnexpectedAdmissionError", logs)
        self.assertIn("Pod was rejected", logs)
        self.assertIn("running triton log", logs)

    def test_ResolveServiceUrls_IngressStatus_ReturnsExternalAddress(self) -> None:
        # Arrange
        ingress = SimpleNamespace(
            spec=SimpleNamespace(
                rules=[
                    SimpleNamespace(
                        http=SimpleNamespace(
                            paths=[
                                SimpleNamespace(
                                    backend=SimpleNamespace(
                                        service=SimpleNamespace(name="triton-service"),
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
            status=SimpleNamespace(
                load_balancer=SimpleNamespace(ingress=[SimpleNamespace(ip="192.168.49.10")]),
            ),
        )
        api_client = object()

        # Act
        with patch("kubernetes.client.NetworkingV1Api") as networking_api:
            networking_api.return_value.list_namespaced_ingress.return_value = SimpleNamespace(items=[ingress])
            urls = k8s.resolve_service_urls(api_client, "triton", "triton-service")

        # Assert
        self.assertEqual(urls["http"], "http://192.168.49.10")
        self.assertEqual(urls["metrics"], "http://192.168.49.10/metrics")


if __name__ == "__main__":
    unittest.main()
