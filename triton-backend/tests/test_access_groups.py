import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core import installation_policy
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import TeamEntity, TritonInstanceEntity, UserEntity, UserTeamEntity
from app.db.schema import create_tables
from app.main import app


class AccessGroupsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        self.addCleanup(engine.dispose)
        group_tables = {"access_groups", "access_group_members", "access_group_instances"}
        SQLModel.metadata.create_all(
            engine, tables=[table for table in SQLModel.metadata.sorted_tables if table.name not in group_tables],
        )
        session = Session(engine)
        self.addCleanup(session.close)
        direct = TritonInstanceEntity(name="direct", url="http://direct")
        inherited = TritonInstanceEntity(name="inherited", url="http://inherited")
        user = UserEntity(
            email="viewer@example.invalid", name="Viewer", role="viewer",
            auth_provider="oidc", assigned_instances=["direct"],
        )
        local = UserEntity(
            email="local@example.invalid", name="Local", assigned_instances=["direct"],
        )
        team = TeamEntity(name="Engineering")
        session.add_all([direct, inherited, user, local, team])
        session.flush()
        session.add(UserTeamEntity(user_id=user.id, team_id=team.id))
        session.commit()
        create_tables(engine)
        self.direct_id, self.inherited_id = direct.id, inherited.id
        self.user_id, self.email = user.id, user.email
        self.local_id = local.id
        self.engine = engine
        self.claims = {"role": "admin"}

        def request_session():
            with Session(engine) as current:
                yield current

        overrides = {get_session: request_session, get_claims: lambda: self.claims}
        self.enterContext(patch.dict(app.dependency_overrides, overrides))
        self.mode = self.enterContext(patch.object(installation_policy, "mode", return_value="production"))
        self.enterContext(patch(
            "app.services.triton.instances.discover_instance_repository_backends", return_value="",
        ))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)

    async def _create(self, name):
        response = await self.client.post("/api/access-groups", json={
            "name": name,
            "user_ids": [self.user_id],
            "instance_ids": [self.direct_id, self.inherited_id],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_overlapping_grants_and_final_revocation(self):
        # Arrange
        first, second = await self._create("First"), await self._create("Second")
        instance_path = f"/api/instances/{self.inherited_id}"
        viewer = {"role": "viewer", "email": self.email, "sub": self.email}
        self.claims = viewer
        self.assertEqual((await self.client.get(instance_path)).status_code, 200)

        # Act
        self.claims = {"role": "admin"}
        first_url = f"/api/access-groups/{first['id']}"
        self.assertEqual((await self.client.put(first_url, json={**first, "user_ids": []})).status_code, 200)
        self.claims = viewer
        after_membership_removed = await self.client.get(instance_path)
        self.claims = {"role": "admin"}
        self.assertEqual((await self.client.delete(
            f"/api/access-groups/{second['id']}", params={"revision": second["revision"]},
        )).status_code, 204)
        self.claims = viewer
        after_group_deleted = await self.client.get(instance_path)
        visible = await self.client.get("/api/instances")

        # Assert
        self.assertEqual(after_membership_removed.status_code, 200)
        self.assertEqual(after_group_deleted.status_code, 403)
        self.assertEqual(visible.status_code, 200)
        self.assertEqual([item["name"] for item in visible.json()], ["direct"])

    async def test_engineering_membership_grants_no_scope_or_group_admin(self):
        # Arrange
        self.claims = {"role": "viewer", "email": self.email, "sub": self.email}

        # Act
        direct = await self.client.get(f"/api/instances/{self.direct_id}")
        inherited = await self.client.get(f"/api/instances/{self.inherited_id}")
        group_list = await self.client.get("/api/access-groups")
        create = await self.client.post("/api/access-groups", json={"name": "Unauthorized"})

        # Assert
        self.assertEqual(direct.status_code, 200)
        self.assertEqual(inherited.status_code, 403)
        self.assertEqual(group_list.status_code, 403)
        self.assertEqual(create.status_code, 403)

    async def test_stale_admin_edit_cannot_restore_revoked_access(self):
        # Arrange
        group = await self._create("Vision")
        url = f"/api/access-groups/{group['id']}"
        self.assertEqual((await self.client.put(url, json={**group, "instance_ids": []})).status_code, 200)

        # Act
        stale_save = await self.client.put(url, json=group)
        latest = (await self.client.get("/api/access-groups")).json()

        # Assert
        self.assertEqual(stale_save.status_code, 409)
        self.assertEqual(latest[0]["instance_ids"], [])

    async def test_upgrade_preserves_local_and_oidc_direct_assignments(self):
        # Arrange
        expected = {self.user_id: ["direct"], self.local_id: ["direct"]}

        # Act
        response = await self.client.get("/api/auth/users")

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual({user["id"]: user["assigned_instances"] for user in response.json()}, expected)

    async def test_dev_spoke_denies_group_administration_and_ignores_inherited_access(self):
        # Arrange
        group = await self._create("Vision")
        self.mode.return_value = "development"
        url = f"/api/access-groups/{group['id']}"

        # Act
        admin_responses = [
            await self.client.get("/api/access-groups"),
            await self.client.get("/api/access-groups/instance-options"),
            await self.client.post("/api/access-groups", json={"name": "Other"}),
            await self.client.put(url, json=group),
            await self.client.delete(url, params={"revision": group["revision"]}),
        ]
        self.claims = {"role": "viewer", "email": self.email}
        inherited = await self.client.get(f"/api/instances/{self.inherited_id}")
        visible = await self.client.get("/api/instances")

        # Assert
        self.assertEqual([response.status_code for response in admin_responses], [403] * 5)
        self.assertEqual(inherited.status_code, 403)
        self.assertEqual([item["name"] for item in visible.json()], ["direct"])

    async def test_group_access_requires_viewer_role(self):
        # Arrange
        group = await self._create("Vision")
        with Session(self.engine) as session:
            for user_id in (self.user_id, self.local_id):
                user = session.get_one(UserEntity, user_id)
                user.role = "member"
            session.commit()

        # Act
        add_member = await self.client.put(
            f"/api/access-groups/{group['id']}", json={**group, "user_ids": [self.user_id, self.local_id]},
        )
        self.claims = {"role": "member", "email": self.email}
        inherited = await self.client.get(f"/api/instances/{self.inherited_id}")
        direct = await self.client.get(f"/api/instances/{self.direct_id}")

        # Assert
        self.assertEqual(add_member.status_code, 422)
        self.assertEqual(inherited.status_code, 403)
        self.assertEqual(direct.status_code, 200)
