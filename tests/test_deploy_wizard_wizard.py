import unittest

from deploy_wizard.config import parse_proxy_route
from deploy_wizard.wizard import (
    _build_compose_path_routes,
    _build_compose_subdomain_routes,
)


class DeployWizardWizardTests(unittest.TestCase):
    def test_build_compose_path_routes_skips_services_without_ports(self) -> None:
        routes = _build_compose_path_routes(
            host="apps.example.org",
            services=["orchestrator", "workflow-studio", "nats", "mongo"],
            service_ports={
                "orchestrator": 8080,
                "workflow-studio": 8000,
            },
        )
        self.assertEqual(len(routes), 2)
        parsed = [parse_proxy_route(item) for item in routes]
        self.assertEqual(parsed[0].host, "apps.example.org")
        self.assertEqual(parsed[0].path_prefix, "/orchestrator")
        self.assertEqual(parsed[0].upstream_host, "orchestrator")
        self.assertEqual(parsed[0].upstream_port, 8080)
        self.assertEqual(parsed[1].path_prefix, "/workflow-studio")

    def test_build_compose_subdomain_routes_skips_services_without_ports(self) -> None:
        routes = _build_compose_subdomain_routes(
            domain="example.org",
            services=["orchestrator", "workflow-studio", "nats", "mongo"],
            service_ports={
                "orchestrator": 8080,
                "workflow-studio": 8000,
            },
        )
        self.assertEqual(len(routes), 2)
        parsed = [parse_proxy_route(item) for item in routes]
        self.assertEqual(parsed[0].host, "orchestrator.example.org")
        self.assertEqual(parsed[0].path_prefix, "/")
        self.assertEqual(parsed[1].host, "workflow-studio.example.org")
        self.assertEqual(parsed[1].path_prefix, "/")

    def test_build_compose_subdomain_routes_deduplicates_labels(self) -> None:
        routes = _build_compose_subdomain_routes(
            domain="example.org",
            services=["my_service", "my-service"],
            service_ports={
                "my_service": 8001,
                "my-service": 8002,
            },
        )
        parsed = [parse_proxy_route(item) for item in routes]
        self.assertEqual(parsed[0].host, "my-service.example.org")
        self.assertEqual(parsed[1].host, "my-service-2.example.org")


if __name__ == "__main__":
    unittest.main()
