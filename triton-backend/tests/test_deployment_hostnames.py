"""Hostname preflight checks across namespaces and controller classes."""

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
            spec=client.V1IngressSpec(ingress_class_name=cls, rules=[client.V1IngressRule(host=host)]),
        )

    def network(self, route):
        net = Mock()
        net.list_ingress_for_all_namespaces.return_value.items = [route]
        return net

    def test_dns_and_single_label_wildcards(self):
        # Arrange
        cases = [
            ("MODEL.example.com.", "model.example.com", True),
            ("*.example.com", "model.example.com", True),
            ("*.example.com", "example.com", False),
            ("*.example.com", "a.b.example.com", False),
            ("*.example.com", "*.example.com", True),
            ("*.example.com", "*.other.example", False),
            ("model.example.com", "other.example.com", False),
            ("", "model.example.com", True),
        ]
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                # Act
                forward, backward = hosts_overlap(first, second), hosts_overlap(second, first)
                # Assert
                self.assertEqual((forward, backward), (expected, expected))

    def test_overlapping_routes_in_other_namespace_conflict(self):
        for host in ("model.example.com", "*.example.com", None):
            with self.subTest(host=host):
                # Arrange
                net = self.network(self.route(host=host))
                # Act
                with self.assertRaises(ConflictError) as raised:
                    validate_hostname_available(net, "model.example.com", "traefik")
                # Assert
                self.assertIn("overlaps", str(raised.exception))

    def test_another_traefik_class_conflicts(self):
        # Arrange
        net = self.network(self.route(cls="public-traefik"))
        net.read_ingress_class.return_value.spec.controller = "traefik.io/ingress-controller"
        # Act
        with self.assertRaises(ConflictError) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertIn("overlaps", str(raised.exception))
        self.assertEqual(net.read_ingress_class.call_args.args, ("public-traefik",))

    def test_other_controller_does_not_conflict(self):
        # Arrange
        net = self.network(self.route(cls="nginx"))
        net.read_ingress_class.return_value.spec.controller = "k8s.io/ingress-nginx"
        # Act
        result = validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertIsNone(result)
        net.read_ingress_class.assert_called_once()

    def test_unclassified_route_conflicts(self):
        # Arrange
        net = self.network(self.route(cls=None))
        # Act
        with self.assertRaises(ConflictError) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertIn("overlaps", str(raised.exception))
        net.read_ingress_class.assert_not_called()

    def test_legacy_class_annotation_uses_controller_lookup(self):
        # Arrange
        route = self.route(cls=None)
        route.metadata.annotations = {"kubernetes.io/ingress.class": "public-traefik"}
        net = self.network(route)
        net.read_ingress_class.return_value.spec.controller = "traefik.io/ingress-controller"
        # Act
        with self.assertRaises(ConflictError) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertIn("overlaps", str(raised.exception))

    def test_missing_class_is_included_conservatively(self):
        # Arrange
        net = self.network(self.route(cls="legacy"))
        net.read_ingress_class.side_effect = ApiException(status=404)
        # Act
        with self.assertRaises(ConflictError) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertIn("overlaps", str(raised.exception))

    def test_class_lookup_failure_does_not_mean_hostname_available(self):
        # Arrange
        net = self.network(self.route(cls="private-traefik"))
        net.read_ingress_class.side_effect = ApiException(status=403)
        # Act
        with self.assertRaises(ApiException) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertEqual(raised.exception.status, 403)

    def test_edit_excludes_owned_route(self):
        # Arrange
        net = self.network(self.route())
        # Act
        result = validate_hostname_available(
            net, "model.example.com", "traefik", existing_ingress=("other", "other-ingress"),
        )
        # Assert
        self.assertIsNone(result)

    def test_edit_does_not_exclude_same_name_in_other_namespace(self):
        # Arrange
        net = self.network(self.route())
        # Act
        with self.assertRaises(ConflictError) as raised:
            validate_hostname_available(
                net, "model.example.com", "traefik", existing_ingress=("control", "other-ingress"),
            )
        # Assert
        self.assertIn("overlaps", str(raised.exception))

    def test_internal_access_does_not_query_kubernetes(self):
        # Arrange
        net = Mock()
        # Act
        validate_hostname_available(net, None, "traefik")
        # Assert
        net.list_ingress_for_all_namespaces.assert_not_called()

    def test_cannot_list_does_not_mean_hostname_available(self):
        # Arrange
        net = Mock()
        net.list_ingress_for_all_namespaces.side_effect = ApiException(status=403)
        # Act
        with self.assertRaises(ApiException) as raised:
            validate_hostname_available(net, "model.example.com", "traefik")
        # Assert
        self.assertEqual(raised.exception.status, 403)
