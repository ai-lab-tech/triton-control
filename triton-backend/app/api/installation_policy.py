"""Semantic production route allowlist, including aliases and WebSocket entry points.

New endpoints fail closed in production until explicitly classified here.
Service checks remain necessary for non-HTTP callers and background execution.
"""

from collections.abc import Sequence

from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import installation_policy

# Exact endpoint identities, not HTTP verb or broad path-prefix permissions.
PRODUCTION_ENDPOINTS = {
    "app.main.root", "app.main.health_check", "app.main.auth_me",
    "fastapi.applications.openapi", "fastapi.applications.swagger_ui_html",
    "fastapi.applications.swagger_ui_redirect", "fastapi.applications.redoc_html",
}
_REVIEWED = {
    "capabilities_api": "read_capabilities",
    "oidc_api": "login auth_callback logout whoami",
    "auth_api": (
        "auth_options_endpoint bootstrap_status_endpoint self_register_endpoint login_endpoint "
        "inspect_invitation_endpoint activate_invitation_endpoint forgot_password_endpoint inspect_reset_endpoint "
        "complete_reset_endpoint get_oidc_settings_endpoint register_user_endpoint invite_user_endpoint "
        "reissue_invitation_endpoint revoke_invitation_endpoint admin_reset_endpoint get_email_settings_endpoint "
        "update_email_settings_endpoint test_email_endpoint"
    ),
    "user_api": "list_users delete_user update_user_instances update_user_role",
    "team_api": "list_teams create_team initialize_engineering_group update_team list_members add_member "
                "remove_member list_history",
    "instance_api": "list_instances get_instance get_instance_by_name",
    "model_api": "get_instance_models get_instance_model_config",
    "s3_api": "get_instance_s3 update_instance_s3 list_instance_s3 get_instance_s3_content get_instance_s3_content_raw",
    "pod_api": "instance_pods instance_pod_logs",
    "deployment_api": "get_deployment_policy get_deployment_logs get_deployment_access",
    "dashboard_api": "list_dashboard_alerts",
    "error_log_api": "list_error_logs create_frontend_error_log",
    "registry_api": "get_registry_settings",
    "local_bindings_api": "get_policy get_credential put_credential delete_credential admit",
    "s3_profile_api": "resolve_s3_profile list_s3_profiles update_s3_profile delete_s3_profile",
}
PRODUCTION_ENDPOINTS.update(
    f"app.api.{module}.{name}" for module, names in _REVIEWED.items() for name in names.split()
)


class InstallationPolicyMiddleware:
    def __init__(self, app: ASGIApp, routes: Sequence[BaseRoute]) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or installation_policy.mode() == "development":
            await self.app(scope, receive, send)
            return
        for route in self.routes:
            match, child = route.matches(scope)
            if match == Match.FULL:
                endpoint = child.get("endpoint")
                identity = f"{getattr(endpoint, '__module__', '')}.{getattr(endpoint, '__name__', '')}"
                if identity in PRODUCTION_ENDPOINTS:
                    await self.app(scope, receive, send)
                    return
                break
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008,
                        "reason": "Unavailable in production mode"})
        else:
            response = JSONResponse(status_code=403, content={
                "detail": "Unavailable in production mode. Use an independently approved serving change.",
            })
            await response(scope, receive, send)
