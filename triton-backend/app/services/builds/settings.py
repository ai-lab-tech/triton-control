"""Interactive administrator management of the development build policy."""

import json
import os
from typing import Any
from uuid import uuid4

from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, ForbiddenError
from app.repositories import build_config
from app.schemas.build_policy import (
    BuildDestination,
    BuildDestinationSettings,
    BuildDestinationUpdate,
    BuilderImage,
    BuildPolicy,
    BuildPolicySettings,
)
from app.services import registry_settings
from app.services.builds import contexts_storage
from app.services.builds.execution import qualified
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
        return BuildPolicySettings(policy=contexts_storage.policy(session), source="admin")
    if os.getenv("TRITON_BUILD_POLICY_FILE"):
        return BuildPolicySettings(policy=contexts_storage.policy(session), source="deployment")
    return BuildPolicySettings(policy=None, source="unconfigured")


def validate_namespace(namespace: str) -> None:
    if namespace in {"default", "kube-system", "kube-public", "kube-node-lease", os.getenv("POD_NAMESPACE")}:
        raise BadRequestError("Choose a dedicated build namespace")


def update(session: Session, policy: BuildPolicy | None) -> BuildPolicySettings:
    values = None
    if policy:
        inherited = {"release", "executor_image"} - policy.model_fields_set
        values = policy.model_dump(mode="json", exclude=inherited)
        policy = contexts_storage.with_default_executor(policy)
        validate_namespace(policy.namespace)
        contexts_storage.binding(session, policy)
        if policy.enabled:
            revision = os.getenv("TRITON_BUILD_FIXTURE_REVISION")
            if not revision:
                raise BadRequestError("Configure the reviewed fixture revision before enabling builds")
            require_qualified(session, policy, revision, mode=installation_policy.mode())
    elif os.getenv("TRITON_BUILD_POLICY_FILE"):
        contexts_storage.deployment_policy()
    build_config.save(session, values)
    return read(session)


def destination_settings(session: Session) -> BuildDestinationSettings:
    row = build_config.get_destination(session)
    configured = build_config.get(session) is not None or bool(os.getenv("TRITON_BUILD_POLICY_FILE"))
    effective = contexts_storage.base_policy(session) if configured else None
    builder = (BuilderImage(executor_image=effective.executor_image, release=effective.release)
               if effective else contexts_storage.default_executor())
    destination = None
    if row:
        destination = BuildDestination(namespace=row.namespace, allowed_repositories=tuple(row.repositories),
                                       push_secret=None if row.push_config_enc else row.push_secret)
    elif effective:
        destination = BuildDestination(namespace=effective.namespace,
                                       allowed_repositories=effective.allowed_repositories)
    status = "The cluster builder still needs setup."
    if not configured and os.getenv("TRITON_LOCAL_BUILD_NAMESPACE"):
        if row and row.repositories:
            status = "Registry publishing requires a registry builder. Clear repositories to build a local archive."
        else:
            status = f"Local images are saved in {os.environ['TRITON_LOCAL_BUILD_NAMESPACE']} as image.tar."
    if configured:
        try:
            qualified(session)
            status = "Builder qualified."
        except ForbiddenError as error:
            status = error.detail
    return BuildDestinationSettings(builder=builder, destination=destination, has_override=row is not None,
                                    status=status,
                                    push_credentials_saved=bool(row and row.push_config_enc),
                                    reuse_pull_credentials=bool(row and row.reuse_pull_credentials))


def set_destination(session: Session, destination: BuildDestinationUpdate | None) -> BuildDestinationSettings:
    if destination:
        validate_namespace(destination.namespace)
        if destination.push_secret and (build_config.get(session) or os.getenv("TRITON_BUILD_POLICY_FILE")):
            base = contexts_storage.base_policy(session)
            if destination.push_secret in (base.base_pull_secret, base.runtime_pull_secret):
                raise BadRequestError("Choose a push secret separate from the builder pull secrets")
        if destination.reuse_pull_credentials:
            contexts_storage.saved_pull_credentials(session)
        reference = destination.push_secret
        encrypted = None
        if destination.dockerconfigjson is not None:
            encrypted = ""
            if destination.dockerconfigjson.strip():
                auths = json.loads(destination.dockerconfigjson)["auths"]
                if any(not auth for auth in auths.values()):
                    raise BadRequestError("Each registry entry must include push credentials")
                encrypted = registry_settings.encrypt_registry_secret(json.dumps({"auths": auths}))
                reference = "managed-push-" + uuid4().hex
        build_config.save_destination(session, destination.namespace, destination.allowed_repositories,
                                      reference, encrypted, destination.reuse_pull_credentials)
    else:
        row = build_config.get_destination(session)
        if row:
            session.delete(row)
            session.commit()
    return destination_settings(session)
