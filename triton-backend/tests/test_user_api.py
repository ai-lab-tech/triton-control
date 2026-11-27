"""Unit tests for the auth and user management API endpoints.

Covers:
  Bootstrap flow           — bootstrap status, bootstrap registration.
  Local login              — success, wrong password, OIDC-disabled guard.
  User registration        — admin-only create, self-register, pending path.
  OIDC preflight           — start and callback routes.
  User management (admin)  — list, delete, update instances, update role.

All service calls and DB sessions are replaced with mocks; no HTTP server
or database is required.
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from authlib.integrations.base_client import OAuthError
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.auth_api import (
    auth_options,
    bootstrap_register,
    bootstrap_status,
    login,
    oidc_preflight_callback,
    put_oidc_settings,
    register_user,
    self_register,
    start_oidc_preflight,
)
from app.api.user_api import (
    delete_user,
    list_users,
    update_user_instances,
    update_user_role,
)
from app.core.access_control import require_admin, require_member_or_admin
from app.db.entities import UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.schemas import (
    ROLE_ALIASES,
    BootstrapRegisterRequest,
    CreateUserRequest,
    LoginRequest,
    OidcSettingsDTO,
    SelfRegisterRequest,
    UpdateOidcSettingsRequest,
    UpdateUserInstancesRequest,
    UpdateUserRoleRequest,
)
from app.services.auth.local_auth import has_active_local_admin_login as _has_active_local_admin_login
from app.services.auth.oidc_preflight import (
    OIDC_PREFLIGHT_SESSION_KEY,
)
from app.services.auth.oidc_preflight import (
    build_oidc_preflight_context as _build_oidc_preflight_context,
)


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
        self.rollback_count = 0

    def get(self, _model, key):
        return self.get_map.get(key)

    def exec(self, _query):
        return self.exec_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.commit_count += 1

    def refresh(self, obj):
        if hasattr(obj, "id") and getattr(obj, "id") is None:
            obj.id = 1
        self.refresh_count += 1

    def rollback(self):
        self.rollback_count += 1


class _Req:
    def __init__(self):
        self.session = {}
        self.query_params = {}
        self.url = "http://backend/settings/preflight/callback"

    def url_for(self, name):
        return f"http://backend/{name}"


class UserApiHelperTests(unittest.TestCase):
    def test_public_registration_leaves_existing_local_accounts_untouched(self):
        request = SelfRegisterRequest(email="reserved@example.com", password="Validpass123!", name="New")
        for role, password in (("admin", None), ("member", None), ("viewer", "existing-hash")):
            with self.subTest(role=role):
                user = UserEntity(
                    id=7, email=request.email, name="Reserved", role=role, auth_provider="local",
                    password_hash=password, assigned_instances=["instance-a"], is_active=password is not None,
                )
                session = _FakeSession(exec_results=[_ExecResult(first=user)])
                original = user.model_dump()
                with (
                    patch("app.services.auth.local_auth.ensure_local_auth_allowed"),
                    patch("app.services.auth.local_auth.hash_password") as hash_password,
                    self.assertRaises(ConflictError),
                ):
                    self_register(request, session)

                hash_password.assert_not_called()
                self.assertEqual(user.model_dump(), original)
                self.assertEqual(session.commit_count, 0)

    def test_UserApiHelpers_MixedInputs_ProduceExpectedNormalizationAndGuards(self):
        # Arrange / Act
        normalized_member = ROLE_ALIASES.get("ml engineer", "")
        normalized_invalid = ROLE_ALIASES.get("bad", "")

        # Assert
        self.assertEqual(normalized_member, "member")
        self.assertEqual(normalized_invalid, "")

        # Act / Assert
        with self.assertRaises(ForbiddenError):
            require_admin({"role": "viewer"})
        with self.assertRaises(ForbiddenError):
            require_member_or_admin({"role": "viewer"})
        require_member_or_admin({"role": "member"})

        # Arrange
        session = _FakeSession(exec_results=[_ExecResult(first=1)])

        # Act
        has_active_local_admin = _has_active_local_admin_login(session)

        # Assert
        self.assertTrue(has_active_local_admin)

        # Act
        ctx = _build_oidc_preflight_context(
            UpdateOidcSettingsRequest(
                oidc_enabled=True,
                issuer=" http://iss ",
                client_id=" cid ",
                client_secret=" sec ",
                redirect_uri=" http://cb ",
                scopes=" openid ",
                strict_discovery_document_validation=False,
                api_base_url=" http://api ",
            ),
            {"email": "Admin@Example.com", "name": "Admin"},
        )

        # Assert
        self.assertEqual(ctx["expected_email"], "admin@example.com")

    def test_AuthBootstrapEndpoints_DifferentSystemStates_ReturnExpectedResponses(self):
        # Arrange
        session = _FakeSession()

        # Act
        with patch("app.services.auth.bootstrap.get_settings", return_value=SimpleNamespace(oidc_enabled=True)):
            options = auth_options(session)
            status = bootstrap_status(session)

        # Assert
        self.assertEqual(options["oidc_enabled"], True)
        self.assertTrue(status.oidc_enabled)

        # Arrange
        session = _FakeSession(exec_results=[_ExecResult(all_rows=[])])

        # Act
        with patch("app.services.auth.bootstrap.get_settings", return_value=SimpleNamespace(oidc_enabled=False)):
            status = bootstrap_status(session)

        # Assert
        self.assertTrue(status.needs_setup)

        # Arrange
        session = _FakeSession(exec_results=[_ExecResult(all_rows=[])])
        req = BootstrapRegisterRequest(email="admin@example.com", password="Validpass123!", name="Admin")

        # Act
        with (
            patch("app.services.auth.bootstrap.get_settings", return_value=SimpleNamespace(oidc_enabled=False)),
            patch("app.services.auth.bootstrap.hash_password", return_value="hashed"),
        ):
            dto = bootstrap_register(req, session)

        # Assert
        self.assertEqual(dto.email, "admin@example.com")

        # Act / Assert
        with patch("app.services.auth.bootstrap.get_settings", return_value=SimpleNamespace(oidc_enabled=True)):
            with self.assertRaises(BadRequestError):
                bootstrap_register(req, session)


class UserApiRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_OidcSettingsUpdate_EnvAndDbModes_ReturnsPreflightBehavior(self):
        # Arrange
        req = _Req()
        settings = SimpleNamespace(
            oidc_enabled=False,
            issuer="https://iss",
            client_id="cid",
            client_secret="sec",
            redirect_uri="http://cb",
            scopes="openid",
            strict_discovery_document_validation=False,
            ca_certificate="",
            api_base_url="http://api",
        )
        update_req = UpdateOidcSettingsRequest(
            oidc_enabled=True,
            issuer="https://iss",
            client_id="cid",
            client_secret="sec",
            redirect_uri="http://cb",
            scopes="openid",
            strict_discovery_document_validation=True,
            ca_certificate="cert",
            api_base_url="http://api",
        )

        # Act / Assert
        with patch("app.services.auth.oidc_settings.is_env_config_source", return_value=True):
            with self.assertRaises(BadRequestError):
                await put_oidc_settings(update_req, req, _FakeSession(), {"role": "admin"})

        # Act
        with (
            patch("app.services.auth.oidc_settings.is_env_config_source", return_value=False),
            patch("app.services.auth.oidc_settings.get_settings", return_value=settings),
            patch("app.services.auth.oidc_settings.validate_oidc_connection"),
            patch("app.services.auth.oidc_settings.build_oidc_preflight_context", return_value={"settings": {}}),
            patch(
                "app.services.auth.oidc_settings.start_preflight_redirect",
                AsyncMock(return_value={"preflight_required": True}),
            ),
        ):
            response = await put_oidc_settings(
                update_req, req, _FakeSession(), {"role": "admin", "email": "admin@example.com"}
            )

        # Assert
        self.assertTrue(response["preflight_required"])

        # Act
        with (
            patch("app.services.auth.oidc_preflight.is_env_config_source", return_value=False),
            patch("app.services.auth.oidc_preflight.validate_oidc_connection"),
            patch("app.services.auth.oidc_preflight.build_oidc_preflight_context", return_value={"settings": {}}),
            patch(
                "app.services.auth.oidc_preflight.start_preflight_redirect",
                AsyncMock(return_value={"preflight_required": True}),
            ),
        ):
            started = await start_oidc_preflight(update_req, req, {"role": "admin", "email": "admin@example.com"})

        # Assert
        self.assertTrue(started["preflight_required"])

    async def test_OidcSettings_BlankClientSecret_PreservesExistingSecretAndRedactsResponse(self):
        # Arrange
        current = OidcSettingsDTO(
            oidc_enabled=True,
            issuer="https://iss",
            client_id="cid",
            client_secret="old-sec",
            redirect_uri="http://cb",
            scopes="openid",
            strict_discovery_document_validation=False,
            ca_certificate="",
            api_base_url="http://api",
            config_source="db",
        )
        update_req = UpdateOidcSettingsRequest(
            oidc_enabled=True,
            issuer="https://iss",
            client_id="cid",
            client_secret="",
            redirect_uri="http://cb",
            scopes="openid",
            strict_discovery_document_validation=False,
            ca_certificate="",
            api_base_url="http://api",
        )

        def _save(_session, request):
            return OidcSettingsDTO(
                oidc_enabled=request.oidc_enabled,
                issuer=request.issuer,
                client_id=request.client_id,
                client_secret=request.client_secret,
                redirect_uri=request.redirect_uri,
                scopes=request.scopes,
                strict_discovery_document_validation=request.strict_discovery_document_validation,
                ca_certificate=request.ca_certificate,
                api_base_url=request.api_base_url,
                config_source="db",
            )

        # Act
        with (
            patch("app.services.auth.oidc_settings.is_env_config_source", return_value=False),
            patch("app.services.auth.oidc_settings.get_settings", return_value=current),
            patch("app.services.auth.oidc_settings.validate_oidc_connection") as validate,
            patch("app.services.auth.oidc_settings.update_settings", side_effect=_save) as save,
        ):
            response = await put_oidc_settings(
                update_req,
                _Req(),
                _FakeSession(),
                {"role": "admin", "email": "admin@example.com"},
            )

        # Assert
        saved_request = save.call_args.args[1]
        self.assertEqual(saved_request.client_secret, "old-sec")
        self.assertEqual(response.client_secret, "")
        self.assertTrue(response.client_secret_configured)
        validate.assert_not_called()

    async def test_OidcPreflightCallback_SuccessAndFailurePaths_RedirectsAccordingly(self):
        # Arrange
        request = _Req()
        session = _FakeSession(exec_results=[_ExecResult(first=None), _ExecResult(first=None)])
        request.session[OIDC_PREFLIGHT_SESSION_KEY] = {
            "expected_email": "admin@example.com",
            "local_name": "Admin",
            "settings": {
                "oidc_enabled": True,
                "issuer": "http://iss",
                "client_id": "cid",
                "client_secret": "sec",
                "redirect_uri": "http://cb",
                "scopes": "openid",
                "strict_discovery_document_validation": False,
                "api_base_url": "http://api",
            },
        }
        oauth = SimpleNamespace(
            keycloak=SimpleNamespace(
                authorize_access_token=AsyncMock(return_value={"at": "x"}),
                userinfo=AsyncMock(
                    return_value={
                        "email": "admin@example.com",
                        "sub": "sub1",
                        "name": "Admin",
                        "preferred_username": "admin",
                    }
                ),
            )
        )

        # Act
        with (
            patch("app.services.auth.oidc_preflight.get_frontend_redirect_url", return_value="http://frontend/"),
            patch("app.services.auth.oidc_preflight.load_server_metadata", new_callable=AsyncMock, return_value={}),
            patch("app.services.auth.oidc_preflight.get_oauth", return_value=oauth),
            patch("app.services.auth.oidc_preflight.update_settings"),
        ):
            res = await oidc_preflight_callback(request, session)

        # Assert
        self.assertEqual(res.status_code, 307)
        self.assertNotIn(OIDC_PREFLIGHT_SESSION_KEY, request.session)

        # Act / Assert
        missing_ctx_req = _Req()
        with self.assertRaises(BadRequestError):
            await oidc_preflight_callback(missing_ctx_req, session)

        # Arrange
        request = _Req()
        request.session[OIDC_PREFLIGHT_SESSION_KEY] = {
            "expected_email": "admin@example.com",
            "local_name": "Admin",
            "settings": {},
        }
        oauth_err = SimpleNamespace(
            keycloak=SimpleNamespace(authorize_access_token=AsyncMock(side_effect=OAuthError(error="bad")))
        )

        # Act
        with (
            patch("app.services.auth.oidc_preflight.get_frontend_redirect_url", return_value="http://frontend/"),
            patch("app.services.auth.oidc_preflight.get_oauth", return_value=oauth_err),
        ):
            res = await oidc_preflight_callback(request, session)

        # Assert
        self.assertEqual(res.status_code, 302)

    async def test_UserManagementFlows_LocalAuthAndAdminOps_ReturnExpectedOutcomes(self):
        # Arrange
        admin_claims = {"role": "admin"}
        create_req = CreateUserRequest(
            email="new@example.com",
            name="New",
            role="viewer",
            auth_provider="local",
            creation_mode="inactive",
            assigned_instances=["a"],
        )
        session = _FakeSession(exec_results=[_ExecResult(first=None)])

        # Act
        with patch("app.services.auth.local_auth.oidc_enabled", return_value=False):
            dto = register_user(create_req, session, admin_claims)

        # Assert
        self.assertEqual(dto.email, "new@example.com")
        self.assertFalse(dto.is_active)

        # Arrange
        bad_session = _FakeSession(
            exec_results=[
                _ExecResult(
                    first=UserEntity(
                        email="new@example.com",
                        name="N",
                        role="viewer",
                        auth_provider="local",
                        assigned_instances=[],
                        is_active=True,
                    )
                )
            ]
        )

        # Act / Assert
        with patch("app.services.auth.local_auth.oidc_enabled", return_value=False):
            with self.assertRaises(ConflictError):
                register_user(create_req, bad_session, admin_claims)

        # Arrange
        sr_session = _FakeSession(exec_results=[_ExecResult(first=None)])

        # Act
        with (
            patch("app.services.auth.local_auth.ensure_local_auth_allowed"),
            patch("app.services.auth.local_auth.hash_password", return_value="hashed"),
        ):
            dto = self_register(
                SelfRegisterRequest(email="self@example.com", password="Validpass123!", name="Self"), sr_session
            )

        # Assert
        self.assertEqual(dto.email, "self@example.com")

        # Arrange
        login_session = _FakeSession(
            exec_results=[
                _ExecResult(all_rows=[]),
                _ExecResult(
                    first=UserEntity(
                        email="self@example.com",
                        name="Self",
                        role="viewer",
                        auth_provider="local",
                        password_hash="hashed",
                        assigned_instances=[],
                        is_active=True,
                    )
                ),
            ]
        )

        # Act
        with (
            patch("app.services.auth.local_auth.oidc_enabled", return_value=False),
            patch("app.services.auth.local_auth.hash_password", return_value="hashed"),
            patch("app.services.auth.local_auth.verify_password", return_value=True),
            patch("app.services.auth.local_auth.issue_access_token", return_value="token"),
        ):
            lr = login(LoginRequest(email="self@example.com", password="Validpass123!"), login_session)

        # Assert
        self.assertEqual(lr.access_token, "token")

        # Arrange
        admin = UserEntity(
            id=1,
            email="a@example.com",
            name="A",
            role="admin",
            auth_provider="local",
            assigned_instances=[],
            is_active=True,
        )
        list_session = _FakeSession(
            exec_results=[
                _ExecResult(all_rows=[]),
                _ExecResult(all_rows=[admin]),
            ]
        )

        # Act
        users = list_users(list_session, admin_claims)

        # Assert
        self.assertEqual(len(users), 1)

        # Arrange / Act
        op_session = _FakeSession(
            get_map={1: admin},
            exec_results=[_ExecResult(), _ExecResult()],
        )
        updated = update_user_instances(
            1, UpdateUserInstancesRequest(assigned_instances=["x"]), op_session, admin_claims
        )
        with patch("app.repositories.users.is_last_active_admin_for_update", return_value=False):
            role_updated = update_user_role(1, UpdateUserRoleRequest(role="member"), op_session, admin_claims)
            deleted = delete_user(1, op_session, admin_claims)

        # Assert
        self.assertIsNone(deleted)
        self.assertEqual(updated.assigned_instances, ["x"])
        self.assertEqual(role_updated.role, "member")


class LastActiveAdminTests(unittest.TestCase):
    def setUp(self):
        database = TemporaryDirectory()
        self.addCleanup(database.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(database.name) / 'users.db'}")
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.claims = {"role": "admin"}

    def add_admin(self, email, *, active=True, provider="local"):
        user = UserEntity(email=email, name=email, role="admin", is_active=active, auth_provider=provider)
        self.session.add(user)
        self.session.commit()
        return user.id

    def test_pending_admin_does_not_allow_removing_the_only_active_admin(self):
        # Arrange
        target = self.add_admin("owner@example.test")
        self.add_admin("invited@example.test", active=False)

        for operation in (
            lambda: delete_user(target, self.session, self.claims),
            lambda: update_user_role(target, UpdateUserRoleRequest(role="viewer"), self.session, self.claims),
        ):
            # Act
            with self.assertRaises(HTTPException) as raised:
                operation()
            self.session.rollback()

            # Assert
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(self.session.get(UserEntity, target).role, "admin")

    def test_concurrent_delete_and_demotion_leave_one_admin(self):
        # Arrange
        first = self.add_admin("first@example.test")
        second = self.add_admin("second@example.test")
        start = Barrier(2)

        def remove_admin(user_id):
            with Session(self.engine) as session:
                start.wait(timeout=5)
                try:
                    if user_id == first:
                        delete_user(first, session, self.claims)
                    else:
                        update_user_role(second, UpdateUserRoleRequest(role="member"), session, self.claims)
                except HTTPException as error:
                    return error.status_code
                return 200

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(remove_admin, (first, second)))

        # Assert
        self.assertCountEqual(statuses, [200, 409])
        remaining = self.session.exec(select(UserEntity).where(UserEntity.role == "admin")).all()
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0].is_active)

    def test_second_active_oidc_admin_permits_handoff(self):
        # Arrange
        first = self.add_admin("first@example.test")
        second = self.add_admin("second@example.test", provider="oidc")

        # Act
        update_user_role(first, UpdateUserRoleRequest(role="member"), self.session, self.claims)
        delete_user(first, self.session, self.claims)
        unchanged = update_user_role(second, UpdateUserRoleRequest(role="admin"), self.session, self.claims)

        # Assert
        self.assertIsNone(self.session.get(UserEntity, first))
        self.assertEqual(unchanged.role, "admin")
        self.assertTrue(unchanged.is_active)


if __name__ == "__main__":
    unittest.main()
