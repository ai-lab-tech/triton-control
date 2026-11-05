"""Hostname preflight checks across namespaces and ingress classes."""

import unittest
from unittest.mock import Mock

from kubernetes import client
from kubernetes.client.rest import ApiException

from app.exceptions import ConflictError
from app.services.deployment.hostname import hosts_overlap, validate_hostname_available


class HostnameTests(unittest.TestCase):
    def route(self, host="model.example.com", namespace="other", name="other-ingress", cls="traefik"):
        return client.V1Ingress(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            spec=client.V1IngressSpec(
                ingress_class_name=cls, rules=[client.V1IngressRule(host=host)],
            ),
        )

    def check(self, route, host="model.example.com", **kwargs):
        net = Mock()
        net.list_ingress_for_all_namespaces.return_value.items = [route]
        validate_hostname_available(net, host, "traefik", **kwargs)

    def test_dns_and_single_label_wildcards(self):
        for first, second, expected in [
            ("MODEL.example.com.", "model.example.com", True),
            ("*.example.com", "model.example.com", True),
            ("*.example.com", "example.com", False),
            ("*.example.com", "a.b.example.com", False),
            ("*.example.com", "*.example.com", True),
            ("*.example.com", "*.other.com", False),
            ("model.example.com", "other.example.com", False),
            ("", "model.example.com", True),
        ]:
            with self.subTest(first=first, second=second):
                self.assertEqual(hosts_overlap(first, second), expected)
                self.assertEqual(hosts_overlap(second, first), expected)

    def test_conflict_in_another_namespace(self):
        with self.assertRaises(ConflictError):
            self.check(self.route())

    def test_wildcard_and_hostless_routes_conflict(self):
        for host in ("*.example.com", None):
            with self.subTest(host=host), self.assertRaises(ConflictError):
                self.check(self.route(host=host))

    def test_other_explicit_class_does_not_conflict(self):
        self.check(self.route(cls="nginx"))

    def test_unclassified_routes_are_checked(self):
        with self.assertRaises(ConflictError):
            self.check(self.route(cls=None))

    def test_legacy_class_annotation_is_respected(self):
        route = self.route(cls=None)
        route.metadata.annotations = {"kubernetes.io/ingress.class": "nginx"}
        self.check(route)

    def test_edit_excludes_only_its_own_namespace_and_name(self):
        self.check(self.route(), existing_ingress=("other", "other-ingress"))
        with self.assertRaises(ConflictError):
            self.check(self.route(), existing_ingress=("control", "other-ingress"))

    def test_internal_access_does_not_query_kubernetes(self):
        net = Mock()
        validate_hostname_available(net, None, "traefik")
        net.list_ingress_for_all_namespaces.assert_not_called()

    def test_cannot_list_does_not_mean_hostname_available(self):
        net = Mock()
        net.list_ingress_for_all_namespaces.side_effect = ApiException(status=403)
        with self.assertRaises(ApiException):
            validate_hostname_available(net, "model.example.com", "traefik")
