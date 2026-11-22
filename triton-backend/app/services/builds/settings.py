"""Interactive administrator management of the development build policy."""

import os
from typing import Any

from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, ForbiddenError
from app.repositories import build_config
from app.schemas.build_policy import BuildPolicy, BuildPolicySettings
from app.services.builds import contexts_storage
from app.services.builds.qualification import require_qualified


def authorize(session: Session, claims: dict[str, Any]) -> None:
    installation_policy.require("trust_admin")
    if claims.get("token_use"):
        raise ForbiddenError("Interactive administrator credentials required")
    user = require_user_entity(session, claims)
    if not user.is_active or user.role.strip().lower() != "admin":
        raise ForbiddenError("Build policy management requires an active administrator")


def read(session: Session) -> BuildPolicySettings:
    row = build_config.get(session)
    if row:
        return BuildPolicySettings(policy=BuildPolicy.model_validate(row.policy), source="admin")
    if os.getenv("TRITON_BUILD_POLICY_FILE"):
        return BuildPolicySettings(policy=contexts_storage.deployment_policy(), source="deployment")
    return BuildPolicySettings(policy=None, source="unconfigured")


def update(session: Session, policy: BuildPolicy | None) -> BuildPolicySettings:
    if policy:
        if policy.namespace in {"default", "kube-system", "kube-public", "kube-node-lease", os.getenv("POD_NAMESPACE")}:
            raise BadRequestError("Choose a dedicated build namespace")
        contexts_storage.binding(session, policy)
        if policy.enabled:
            revision = os.getenv("TRITON_BUILD_FIXTURE_REVISION")
            if not revision:
                raise BadRequestError("Configure the reviewed fixture revision before enabling builds")
            require_qualified(session, policy, revision, mode=installation_policy.mode())
    elif os.getenv("TRITON_BUILD_POLICY_FILE"):
        contexts_storage.deployment_policy()
    build_config.save(session, policy.model_dump(mode="json") if policy else None)
    return read(session)
