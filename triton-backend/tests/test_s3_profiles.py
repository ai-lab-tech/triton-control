"""Tests for reusable S3 deployment profiles."""

import unittest

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.entities import S3ProfileEntity, UserEntity
from app.schemas import CreateS3ProfileRequest, UpdateS3ProfileRequest
from app.services.storage import s3_profiles


class S3ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def _session(self) -> Session:
        return Session(self.engine)

    def _claims(self) -> dict[str, object]:
        return {"user_id": 7, "email": "member@example.com", "role": "member"}

    def _create_user(self, session: Session) -> None:
        session.add(
            UserEntity(
                id=7,
                email="member@example.com",
                name="Member",
                role="member",
                auth_provider="local",
                is_active=True,
            )
        )
        session.commit()

    def test_CreateAndListProfiles_ReturnsOwnedCredentials(self) -> None:
        with self._session() as session:
            self._create_user(session)

            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                    prefix="dev",
                ),
            )
            listed = s3_profiles.list_profiles(session, self._claims())

        self.assertEqual(created.endpoint, "https://minio:9000")
        self.assertEqual(created.secret_key, "secret")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].name, "team-minio")
        self.assertEqual(listed[0].secret_key, "secret")

    def test_UpdateProfile_ChangesVisibleFieldsAndCanKeepSecret(self) -> None:
        with self._session() as session:
            self._create_user(session)
            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="https://minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                ),
            )

            updated = s3_profiles.update_profile(
                session,
                self._claims(),
                created.id,
                UpdateS3ProfileRequest(name="prod-minio", bucket="prod-models"),
            )

        self.assertEqual(updated.name, "prod-minio")
        self.assertEqual(updated.bucket, "prod-models")
        self.assertEqual(updated.secret_key, "secret")

    def test_DeleteProfile_RemovesOwnedRow(self) -> None:
        with self._session() as session:
            self._create_user(session)
            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="https://minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                ),
            )

            result = s3_profiles.delete_profile(session, self._claims(), created.id)
            remaining = session.exec(select(S3ProfileEntity)).all()

        self.assertEqual(result, {"status": "deleted"})
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
