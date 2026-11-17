"""Local credential isolation, admission, revocation and API confidentiality."""

import copy
import json
import os
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import LocalCredentialEntity, S3ProfileEntity, UserEntity
from app.db.schema import create_tables
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.main import app
from app.schemas.local_bindings import AdmissionRequest, BindingPolicy, GitCredential, PolicyUpdate, RegistryCredential
from app.services import local_bindings as service
from app.services.lineage import digest

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/serving/v1alpha1/fixtures"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


class LocalBindingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ, {"SESSION_SECRET": "local-binding-test-key", "TRITON_CONTROL_MODE": "development"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = self.make_engine()
        self.addCleanup(self.engine.dispose)
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.admin = UserEntity(email="admin@example.invalid", name="Admin", role="admin")
        self.member = UserEntity(email="member@example.invalid", name="Member", role="member")
        self.viewer = UserEntity(email="viewer@example.invalid", name="Viewer", role="viewer")
        self.session.add_all([self.admin, self.member, self.viewer])
        self.session.commit()
        self.admin_claims = {"email": self.admin.email, "role": "admin"}
        self.claims = {"email": self.member.email, "role": "member"}
        self.profiles = []
        for name, bucket, prefix in (
            ("source", "synthetic-metadata", "targets"),
            ("models", "synthetic-models", "toy"),
        ):
            row = S3ProfileEntity(
                owner_user_id=self.admin.id,
                name=name,
                endpoint="https://storage.example.invalid",
                bucket=bucket,
                prefix=prefix,
                access_key="synthetic-access",
                secret_key_enc="synthetic-storage-password",
            )
            self.session.add(row)
            self.session.commit()
            self.profiles.append(row)
        self.secret = "synthetic-registry-password"
        for name, permission in (("pull-credential", "pull"), ("push-credential", "push")):
            service.put_credential(
                self.session,
                self.admin_claims,
                name,
                RegistryCredential(
                    provider="registry",
                    registry="registry.example.invalid",
                    repository_prefix="synthetic",
                    username="test-user",
                    password=self.secret,
                    permission=permission,
                ),
            )
        self.raw_policy = {
            "source_read": {"metadata-reader": self.binding(self.profiles[0])},
            "model_storage": {"production-models": self.binding(self.profiles[1])},
            "registry_pull": {"serving-registry": {"provider": "registry", "credential": "pull-credential"}},
            "build_push": {"build-registry": {"provider": "registry", "credential": "push-credential"}},
            "sources": {
                "production-s3": {
                    "location": fixture("source-s3")["spec"]["location"],
                    "read_binding": "metadata-reader",
                    "user_ids": [self.member.id],
                }
            },
            "targets": {
                "toy-production": {
                    "source_ids": ["production-s3"],
                    "user_ids": [self.member.id],
                    "model_storage": ["production-models"],
                    "registry_pull": ["serving-registry"],
                    "hardware": ["cpu"],
                    "per_replica": {"cpu": "2", "memory": "1Gi", "gpu": 0},
                    "total": {"cpu": "4", "memory": "2Gi", "gpu": 0},
                    "max_replicas": 4,
                }
            },
            "hardware": {"cpu": {"architecture": "arm64"}},
            "build_user_ids": [self.member.id],
        }
        self.request_data = {
            "source": fixture("source-s3"),
            "target": fixture("target"),
            "release": fixture("release-manual"),
            "hardware": "cpu",
        }

    def make_engine(self):
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    def binding(self, profile):
        return {
            "provider": "s3",
            "profile_id": profile.id,
            "endpoint": profile.endpoint,
            "bucket": profile.bucket,
            "prefix": profile.prefix,
        }

    def configure(self, raw=None, revision=0):
        return service.update_policy(
            self.session,
            self.admin_claims,
            PolicyUpdate(
                expected_revision=revision, policy=BindingPolicy.model_validate(self.raw_policy if raw is None else raw)
            ),
        )

    def request(self, data=None):
        return AdmissionRequest.model_validate(self.request_data if data is None else data)

    def admit(self, data=None, claims=None):
        return service.admit(self.session, self.claims if claims is None else claims, self.request(data))

    def test_default_denies_without_policy(self):
        with self.assertRaises(ForbiddenError):
            self.admit()

    def test_allowed_admission_is_safe_and_uses_exact_release_identity(self):
        self.configure()
        result = self.admit()
        self.assertEqual(result.peak_replicas, 1)
        self.assertEqual(result.release_digest, self.request().target.spec.release.digest)
        self.assertEqual(result.hardware.architecture, "arm64")
        safe = result.model_dump_json() + service.get_policy(self.session, self.admin_claims).model_dump_json()
        for forbidden in (self.secret, "synthetic-storage-password", "synthetic-access", "test-user"):
            self.assertNotIn(forbidden, safe)
        resolved = service.resolve_for_execution(self.session, self.claims, self.request())
        self.assertEqual(resolved["registry-pull"].password.get_secret_value(), self.secret)
        self.assertNotIn(self.secret, repr(resolved))
        self.assertNotIn("synthetic-access", repr(resolved))

    def test_credentials_are_encrypted_and_only_admin_can_manage(self):
        row = self.session.get(LocalCredentialEntity, "pull-credential")
        self.assertNotIn(self.secret, row.secret_enc)
        self.assertNotIn("password", row.scope)
        for claims in (self.claims, {"email": self.viewer.email, "role": "admin"}):
            with self.assertRaises(ForbiddenError):
                service.get_credential(self.session, claims, "pull-credential")
        self.assertNotIn(
            self.secret, service.get_credential(self.session, self.admin_claims, "pull-credential").model_dump_json()
        )

    def test_policy_compare_and_swap_prevents_lost_updates(self):
        self.assertEqual(self.configure().revision, 1)
        with self.assertRaises(ConflictError):
            self.configure()
        self.assertEqual(self.configure(revision=1).revision, 2)
        with self.assertRaises(ConflictError):
            self.configure(revision=1)

    def test_unknown_or_disallowed_references_are_rejected_even_for_admin(self):
        self.configure()
        for group, key, value in (
            ("source", "readCredential", "missing"),
            ("target", "credentials", {"modelStorage": "missing", "imageRegistry": "serving-registry"}),
            ("source", "allowedTargets", ["toy-production", "unmanaged"]),
        ):
            data = copy.deepcopy(self.request_data)
            data[group]["spec"][key] = value
            with self.subTest(key=key), self.assertRaises(ForbiddenError):
                self.admit(data, self.admin_claims)
        for path in ("vault/secret", "secrets/private", "*", "../profile", "Profile"):
            data = copy.deepcopy(self.request_data)
            data["target"]["spec"]["credentials"]["modelStorage"] = path
            with self.subTest(path=path), self.assertRaises(ValidationError):
                self.request(data)

    def test_per_source_and_target_authorization_is_required(self):
        for grant in ("sources", "targets"):
            raw = copy.deepcopy(self.raw_policy)
            next(iter(raw[grant].values()))["user_ids"] = []
            self.configure(raw, revision=0 if grant == "sources" else 1)
            with self.subTest(grant=grant), self.assertRaises(ForbiddenError):
                self.admit()

    def test_role_reactivation_and_delegation_cannot_bypass_checks(self):
        self.configure()
        for token in ("mlflow_workflow", "workspace_deployment", "workflow_deployment"):
            with self.subTest(token=token), self.assertRaises(ForbiddenError):
                self.admit(claims={**self.admin_claims, "token_use": token})
        self.member.is_active = False
        self.session.add(self.member)
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            self.admit()
        self.member.is_active, self.member.role = True, "viewer"
        self.session.add(self.member)
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            self.admit(claims={**self.claims, "role": "admin"})

    def test_profile_scope_changes_and_deletion_revoke_resolution(self):
        self.configure()
        row = self.profiles[1]
        for field, value in (("endpoint", "https://other.invalid"), ("bucket", "other-bucket"), ("prefix", "other")):
            original = getattr(row, field)
            setattr(row, field, value)
            self.session.add(row)
            self.session.commit()
            with self.subTest(field=field), self.assertRaises(ForbiddenError):
                self.admit()
            setattr(row, field, original)
            self.session.add(row)
            self.session.commit()
        self.session.delete(row)
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            self.admit()

    def test_storage_prefix_boundary_blocks_similar_prefixes(self):
        self.configure()
        for uri in (
            "s3://synthetic-models/toy-other/model",
            "s3://other-models/toy/r1/",
            "https://example.invalid/toy",
        ):
            data = copy.deepcopy(self.request_data)
            data["release"]["spec"]["repository"]["uri"] = uri
            data["target"]["spec"]["release"]["digest"] = digest(
                self.request(data).release.model_dump(mode="json", by_alias=True)
            )
            with self.subTest(uri=uri), self.assertRaises(ForbiddenError):
                self.admit(data)

    def test_source_scope_is_pinned_before_fetching(self):
        self.configure()
        source = self.request().source
        self.assertEqual(service.resolve_source_read(self.session, self.claims, source).provider, "s3")
        data = copy.deepcopy(self.request_data)
        data["source"]["spec"]["location"]["key"] = "targets-other/private.json"
        with self.assertRaises(ForbiddenError):
            service.resolve_source_read(self.session, self.claims, self.request(data).source)

    def test_registry_prefix_and_credential_purpose_are_separate(self):
        self.configure()
        result = service.resolve_build_push(
            self.session, self.claims, "build-registry", "registry.example.invalid/synthetic/runtime"
        )
        self.assertEqual(result.provider, "registry")
        for ref, image in (
            ("serving-registry", "registry.example.invalid/synthetic/runtime"),
            ("build-registry", "registry.example.invalid/synthetic-other/runtime"),
            ("build-registry", "other.invalid/synthetic/runtime"),
        ):
            with self.subTest(ref=ref, image=image), self.assertRaises(ForbiddenError):
                service.resolve_build_push(self.session, self.claims, ref, image)
        raw = copy.deepcopy(self.raw_policy)
        raw["registry_pull"]["serving-registry"]["credential"] = "push-credential"
        with self.assertRaises(ForbiddenError):
            self.configure(raw, revision=1)

    def test_registry_image_outside_scope_is_denied(self):
        self.configure()
        data = copy.deepcopy(self.request_data)
        image = data["release"]["spec"]["image"].replace("/synthetic/", "/synthetic-other/")
        data["release"]["spec"]["image"] = image
        data["release"]["spec"]["lineage"]["runtime_image"] = image
        data["target"]["spec"]["release"]["digest"] = digest(
            self.request(data).release.model_dump(mode="json", by_alias=True)
        )
        with self.assertRaises(ForbiddenError):
            self.admit(data)

    def test_mode_checks_do_not_enable_production_builds(self):
        self.configure()
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}):
            self.admit()  # diagnostics only, never workload mutation or approval
            with self.assertRaises(ForbiddenError):
                service.resolve_build_push(
                    self.session, self.admin_claims, "build-registry", "registry.example.invalid/synthetic/runtime"
                )
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "typo"}):
            with self.assertRaises(ForbiddenError):
                self.admit()

    def test_limits_count_limits_not_requests_and_include_surge(self):
        self.configure()
        self.admit()
        for key, value in (("replicas", 5), ("replicas", 4)):
            data = copy.deepcopy(self.request_data)
            data["target"]["spec"][key] = value
            data["target"]["spec"]["rollout"]["maxSurge"] = 1
            with self.subTest(value=value), self.assertRaises(ForbiddenError):
                self.admit(data)
        data = copy.deepcopy(self.request_data)
        data["target"]["spec"]["replicas"] = 3
        data["target"]["spec"]["rollout"]["maxSurge"] = 1
        self.assertEqual(self.admit(data).peak_replicas, 4)
        raw = copy.deepcopy(self.raw_policy)
        raw["targets"]["toy-production"]["per_replica"]["cpu"] = "500m"
        self.configure(raw, revision=1)
        with self.assertRaises(ForbiddenError):
            self.admit()  # request is 250m, limit is 1 CPU

    def test_memory_capacity_exceeded_is_rejected(self):
        raw = copy.deepcopy(self.raw_policy)
        raw["targets"]["toy-production"]["total"]["memory"] = "128Mi"
        self.configure(raw)

        with self.assertRaisesRegex(ForbiddenError, "capacity limit"):
            self.admit()

    def test_gpu_on_cpu_hardware_is_rejected(self):
        self.configure()
        data = copy.deepcopy(self.request_data)
        data["release"]["spec"]["resourcesPerReplica"]["gpuCount"] = 1
        data["target"]["spec"]["release"]["digest"] = digest(
            self.request(data).release.model_dump(mode="json", by_alias=True)
        )

        with self.assertRaisesRegex(ForbiddenError, "hardware GPU limit"):
            self.admit(data)

    def test_unknown_hardware_is_rejected(self):
        self.configure()
        data = copy.deepcopy(self.request_data)
        data["hardware"] = "unknown"

        with self.assertRaisesRegex(ForbiddenError, "hardware is not supported"):
            self.admit(data)

    def test_release_identity_mismatch_is_rejected(self):
        self.configure()
        data = copy.deepcopy(self.request_data)
        data["target"]["spec"]["release"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(BadRequestError):
            self.admit(data)

    def test_git_credentials_are_read_only_and_repository_scoped(self):
        request = GitCredential(
            provider="git",
            repository="https://git.example.invalid/team/repo",
            username="reader",
            password="synthetic-git-password",
            read_only=True,
        )
        service.put_credential(self.session, self.admin_claims, "git-reader", request)
        raw = copy.deepcopy(self.raw_policy)
        location = {"type": "git", "repository": request.repository, "ref": "main", "path": "targets/dev.json"}
        raw["source_read"]["metadata-reader"] = {"provider": "git", "credential": "git-reader"}
        raw["sources"]["production-s3"]["location"] = location
        self.configure(raw)
        data = copy.deepcopy(self.request_data)
        data["source"]["spec"]["location"] = location
        self.assertEqual(
            service.resolve_source_read(self.session, self.claims, self.request(data).source).provider, "git"
        )
        for value in (False, "true"):
            with self.assertRaises(ValidationError):
                GitCredential.model_validate({**request.model_dump(), "read_only": value})
        changed = request.model_copy(update={"repository": "https://git.example.invalid/team/other"})
        with self.assertRaises(ForbiddenError):
            service.put_credential(self.session, self.admin_claims, "git-reader", changed)

    def test_rotation_and_revocation_rechecked_before_execution(self):
        self.configure()
        initial = self.admit()
        credential = RegistryCredential(
            provider="registry",
            registry="registry.example.invalid",
            repository_prefix="synthetic",
            username="test-user",
            password="rotated-synthetic-password",
            permission="pull",
        )
        service.put_credential(self.session, self.admin_claims, "pull-credential", credential)
        resolved = service.resolve_for_execution(self.session, self.claims, self.request())
        self.assertEqual(resolved["registry-pull"].password.get_secret_value(), "rotated-synthetic-password")
        self.assertEqual(initial.policy_digest, self.admit().policy_digest)
        service.delete_credential(self.session, self.admin_claims, "pull-credential")
        with self.assertRaises(ForbiddenError):
            service.resolve_for_execution(self.session, self.claims, self.request())

    def test_corrupt_or_unavailable_encryption_key_is_sanitized(self):
        self.configure()
        with patch.dict(os.environ, {"SESSION_SECRET": "other-synthetic-key"}):
            with self.assertRaises(ForbiddenError) as error:
                self.admit()
        self.assertNotIn(self.secret, str(error.exception))

    def client(self, claims=None):
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.admin_claims if claims is None else claims
        self.addCleanup(app.dependency_overrides.clear)
        return TestClient(app)

    def test_api_admission_returns_no_secrets(self):
        self.configure()
        with closing(self.client()) as client:
            response = client.post("/api/local-bindings/admissions", json=self.request_data)

        self.assertEqual(response.status_code, 200, response.text)
        for secret in (self.secret, "synthetic-storage-password", "synthetic-access", "test-user"):
            self.assertNotIn(secret, response.text)

    def test_api_credential_status_returns_no_secrets(self):
        with closing(self.client()) as client:
            response = client.get("/api/local-bindings/credentials/pull-credential")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.secret, response.text)

    def test_api_validation_error_returns_no_secrets(self):
        with closing(self.client()) as client:
            response = client.put(
                "/api/local-bindings/credentials/new",
                json={
                    "provider": "registry", "registry": "invalid/host", "repository_prefix": "synthetic",
                    "username": "test-user", "password": self.secret, "permission": "pull",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(self.secret, response.text)
        self.assertNotIn("test-user", response.text)

    def test_api_forbids_members_from_admin_configuration(self):
        with closing(self.client(self.claims)) as client:
            self.assertEqual(client.get("/api/local-bindings/policy").status_code, 403)
            self.assertEqual(client.delete("/api/local-bindings/credentials/pull-credential").status_code, 403)

    def test_unsafe_build_destinations_are_not_credential_references(self):
        self.configure()
        for image in (
            "https://registry.example.invalid/synthetic/runtime",
            "registry.example.invalid/synthetic/%2e%2e/private",
            "registry.example.invalid/synthetic/../private",
            "registry.example.invalid/synthetic/runtime:latest",
            "registry.example.invalid/synthetic/runtime@sha256:123",
            "registry.example.invalid/synthetic//runtime",
        ):
            with self.subTest(image=image), self.assertRaises(BadRequestError):
                service.resolve_build_push(self.session, self.claims, "build-registry", image)

    def test_gpu_capacity_and_decimal_quantities_at_exact_boundary(self):
        raw = copy.deepcopy(self.raw_policy)
        raw["hardware"]["gpu"] = {"architecture": "amd64", "accelerator": "nvidia", "max_gpu_per_replica": 2}
        target = raw["targets"]["toy-production"]
        target["hardware"] = ["gpu"]
        target["per_replica"] = {"cpu": "1000m", "memory": "256Mi", "gpu": 2}
        target["total"] = {"cpu": "2000m", "memory": "536870912", "gpu": 4}
        self.configure(raw)
        data = copy.deepcopy(self.request_data)
        data["hardware"] = "gpu"
        data["release"]["spec"]["resourcesPerReplica"]["gpuCount"] = 2
        data["target"]["spec"]["replicas"] = 2
        data["target"]["spec"]["release"]["digest"] = digest(
            self.request(data).release.model_dump(mode="json", by_alias=True)
        )
        self.assertEqual(self.admit(data).peak_replicas, 2)
        data["target"]["spec"]["rollout"]["maxSurge"] = 1
        with self.assertRaises(ForbiddenError):
            self.admit(data)

    def test_policy_validation_rejects_unresolved_and_wrong_purpose_bindings(self):
        for group, key, value in (
            ("registry_pull", "serving-registry", {"provider": "git", "credential": "pull-credential"}),
            ("source_read", "metadata-reader", {"provider": "registry", "credential": "pull-credential"}),
        ):
            raw = copy.deepcopy(self.raw_policy)
            raw[group][key] = value
            with self.subTest(group=group), self.assertRaises(ValidationError):
                BindingPolicy.model_validate(raw)
        raw = copy.deepcopy(self.raw_policy)
        raw["targets"]["toy-production"]["hardware"] = ["unknown"]
        with self.assertRaises(ValidationError):
            BindingPolicy.model_validate(raw)
        raw = copy.deepcopy(self.raw_policy)
        raw["registry_pull"]["serving-registry"]["credential"] = "unknown"
        with self.assertRaises(ForbiddenError):
            self.configure(raw)

    def test_api_admin_configuration_and_rotation_round_trip(self):
        with closing(self.client()) as client:
            response = client.get("/api/local-bindings/policy")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["revision"], 0)
            response = client.put(
                "/api/local-bindings/policy", json={"expected_revision": 0, "policy": self.raw_policy}
            )
            self.assertEqual(response.status_code, 200, response.text)
            response = client.put(
                "/api/local-bindings/policy", json={"expected_revision": 0, "policy": self.raw_policy}
            )
            self.assertEqual(response.status_code, 409)
            response = client.put(
                "/api/local-bindings/credentials/new-reader",
                json={
                    "provider": "git",
                    "repository": "https://git.example.invalid/team/repo",
                    "read_only": True,
                    "username": "reader",
                    "password": "synthetic-git-password",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("synthetic-git-password", response.text)
            self.assertEqual(client.delete("/api/local-bindings/credentials/new-reader").status_code, 204)
            self.assertEqual(client.get("/api/local-bindings/credentials/new-reader").status_code, 404)
