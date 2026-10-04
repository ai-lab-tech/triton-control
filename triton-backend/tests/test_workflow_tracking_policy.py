"""A workflow cannot steal a different identity or the private gateway token."""

import unittest

from app.exceptions import BadRequestError, ForbiddenError
from app.services.workflows.tracking_policy import validate_proxy_write, validate_workflow


class WorkflowTrackingPolicyTests(unittest.TestCase):
    def test_alternate_argo_write_apis_cannot_bypass_validation(self):
        for method, path in (
            ("POST", "api/v1/workflows/control/submit"),
            ("PUT", "api/v1/workflows/control/training"),
            ("POST", "api/v1/cron-workflows/control"),
            ("POST", "api/v1/workflow-templates/control"),
            ("PUT", "api/v1/workflows/control/training/retry"),
        ):
            with self.subTest(path=path), self.assertRaises(ForbiddenError):
                validate_proxy_write(method, path)
        for method, path in (
            ("POST", "api/v1/workflows/control"),
            ("GET", "api/v1/workflows/control"),
            ("DELETE", "api/v1/workflows/control/training"),
            ("PUT", "api/v1/workflows/control/training/terminate"),
        ):
            validate_proxy_write(method, path)

    def workflow(self, template):
        return {"metadata": {"name": "training"}, "spec": {"templates": [{"name": "train", **template}]}}

    def test_owned_s3_credentials_and_workflow_volume_templates_are_allowed(self):
        workflow = self.workflow({"container": {"image": "python:3.12", "env": [{
            "name": "S3_KEY", "valueFrom": {"secretKeyRef": {"name": "owned-s3", "key": "access-key"}},
        }]}})
        workflow["spec"]["serviceAccountName"] = "argo-service-account"
        workflow["spec"]["volumeClaimTemplates"] = [{"metadata": {"name": "scratch"}, "spec": {}}]
        workflow["spec"]["podSpecPatch"] = "automountServiceAccountToken: true"
        validate_workflow(workflow, {"owned-s3"})

    def test_foreign_secrets_cannot_be_mounted_or_projected(self):
        templates = [
            {"container": {"env": [{"valueFrom": {"secretKeyRef": {"name": "mlflow-tracking-gateway"}}}]}},
            {"container": {"envFrom": [{"secretRef": {"name": "another-user-secret"}}]}},
            {"volumes": [{"secret": {"secretName": "mlflow-tracking-gateway"}}]},
            {"volumes": [{"projected": {"sources": [{"secret": {"name": "another-user-secret"}}]}}]},
            {"inputs": {"artifacts": [{"http": {"auth": {"basicAuth": {
                "passwordSecret": {"name": "mlflow-tracking-gateway", "key": "gateway-token"},
            }}}}]}},
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(ForbiddenError):
                validate_workflow(self.workflow(template), {"owned-s3"})

    def test_private_pvc_and_service_account_cannot_bypass_proxy(self):
        templates = [
            {"volumes": [{"persistentVolumeClaim": {"claimName": "mlflow-data"}}]},
            {"serviceAccountName": "triton-control"},
            {"resource": {"action": "get", "manifest": "kind: Secret"}},
            {"templateRef": {"name": "unverified-template"}},
            {"podSpecPatch": "serviceAccountName: triton-control"},
            {"podSpecPatch": 'containers: [{name: main, envFrom: [{secretRef: {name: other}}]}]'},
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(ForbiddenError):
                validate_workflow(self.workflow(template), set())

    def test_host_and_privileged_access_cannot_read_cluster_credentials(self):
        templates = [
            {"hostNetwork": True}, {"hostPID": True}, {"hostIPC": True},
            {"volumes": [{"hostPath": {"path": "/"}}]},
            {"container": {"securityContext": {"privileged": True}}},
            {"container": {"securityContext": {"capabilities": {"add": ["SYS_ADMIN"]}}}},
        ]
        for template in templates:
            with self.subTest(template=template), self.assertRaises(ForbiddenError):
                validate_workflow(self.workflow(template), set())

    def test_claiming_an_existing_pvc_name_does_not_make_it_workflow_owned(self):
        workflow = self.workflow({"volumes": [{"persistentVolumeClaim": {"claimName": "mlflow-data"}}]})
        workflow["spec"]["volumeClaimTemplates"] = [{"metadata": {"name": "mlflow-data"}}]
        with self.assertRaises(ForbiddenError):
            validate_workflow(workflow, set())

    def test_identity_annotation_and_external_workflow_template_are_rejected(self):
        workflow = self.workflow({"container": {"image": "python:3.12"}})
        workflow["metadata"]["annotations"] = {"triton-control.ai/mlflow-owner-user-id": "another-user"}
        with self.assertRaises(ForbiddenError):
            validate_workflow(workflow, set())
        workflow["metadata"].pop("annotations")
        workflow["spec"]["workflowTemplateRef"] = {"name": "unverified"}
        with self.assertRaises(BadRequestError):
            validate_workflow(workflow, set())
