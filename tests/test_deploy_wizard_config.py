import tempfile
import unittest
from pathlib import Path

from deploy_wizard.config import AccessMode, Config, SourceKind, list_compose_services


class DeployWizardConfigTests(unittest.TestCase):
    def test_auto_detects_compose(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            cfg = Config(service_name="svc", source_dir=src)
            self.assertEqual(cfg.source_kind, SourceKind.COMPOSE)
            self.assertEqual(cfg.source_compose_path, src / "docker-compose.yml")

    def test_auto_detects_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(service_name="svc", source_dir=src)
            self.assertEqual(cfg.source_kind, SourceKind.DOCKERFILE)

    def test_no_supported_source_files_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            with self.assertRaises(ValueError):
                Config(service_name="svc", source_dir=src)

    def test_port_mapping_must_be_complete_pair(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.DOCKERFILE,
                    host_port=8080,
                )

    def test_compose_project_name_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            cfg = Config(service_name="My.Service", source_dir=src)
            self.assertEqual(cfg.compose_project_name, "my-service")

    def test_registry_retries_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(service_name="svc", source_dir=src, registry_retries=0)

    def test_retry_backoff_seconds_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(service_name="svc", source_dir=src, retry_backoff_seconds=0)

    def test_list_compose_services_discovers_services(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            compose = src / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n"
                "  worker:\n"
                "    image: example/worker:latest\n",
                encoding="utf-8",
            )
            self.assertEqual(list_compose_services(compose), ["api", "worker"])

    def test_compose_services_must_exist_when_discoverable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    compose_services=("api", "unknown"),
                )

    def test_compose_services_for_dockerfile_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.DOCKERFILE,
                    compose_services=("api",),
                )

    def test_tls_dockerfile_uses_container_port_as_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                container_port=8080,
                host_port=18080,
                access_mode=AccessMode.PUBLIC,
                domain="api.example.com",
                certbot_email="ops@example.com",
            )
            self.assertTrue(cfg.tls_enabled)
            self.assertEqual(cfg.effective_proxy_upstream_service, "svc")
            self.assertEqual(cfg.effective_proxy_upstream_port, 8080)

    def test_tls_compose_requires_upstream_port(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                    domain="api.example.com",
                    certbot_email="ops@example.com",
                )

    def test_proxy_settings_require_domain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    certbot_email="ops@example.com",
                )

    def test_proxy_upstream_service_must_be_selected_if_subset_deployed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n"
                "  worker:\n"
                "    image: example/worker:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    access_mode=AccessMode.PUBLIC,
                    domain="api.example.com",
                    certbot_email="ops@example.com",
                    proxy_upstream_service="worker",
                    proxy_upstream_port=8080,
                    compose_services=("api",),
                )

    def test_auth_token_enables_proxy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                container_port=8080,
                host_port=18080,
                auth_token="TokenABC123",
            )
            self.assertTrue(cfg.reverse_proxy_enabled)
            self.assertEqual(cfg.effective_proxy_upstream_port, 8080)
            self.assertEqual(cfg.effective_proxy_http_port, 80)

    def test_proxy_https_port_requires_domain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.DOCKERFILE,
                    container_port=8080,
                    host_port=18080,
                    auth_token="TokenABC123",
                    proxy_https_port=8443,
                )

    def test_proxy_http_and_https_ports_must_differ(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.DOCKERFILE,
                    container_port=8080,
                    host_port=18080,
                    access_mode=AccessMode.PUBLIC,
                    domain="api.example.com",
                    certbot_email="ops@example.com",
                    proxy_http_port=8081,
                    proxy_https_port=8081,
                )

    def test_compose_public_mode_requires_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                )

    def test_public_access_mode_sets_bind_host(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                access_mode=AccessMode.PUBLIC,
            )
            self.assertEqual(cfg.effective_bind_host, "0.0.0.0")

    def test_proxy_routes_are_parsed_and_cert_domains_include_route_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n"
                "  mail:\n"
                "    image: example/mail:latest\n",
                encoding="utf-8",
            )
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.COMPOSE,
                access_mode=AccessMode.PUBLIC,
                domain="api.example.com",
                certbot_email="ops@example.com",
                proxy_routes=(
                    "wiki.example.com=orchestrator:8090",
                    "mail.example.com=mail:4000",
                ),
            )
            self.assertEqual(len(cfg.effective_proxy_routes), 2)
            self.assertEqual(
                cfg.cert_domain_names,
                ("api.example.com", "wiki.example.com", "mail.example.com"),
            )

    def test_proxy_routes_conflict_with_single_upstream_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                    auth_token="TokenABC123",
                    proxy_routes=("wiki.example.com=orchestrator:8090",),
                    proxy_upstream_service="orchestrator",
                )

    def test_tls_proxy_routes_require_dns_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                    domain="api.example.com",
                    certbot_email="ops@example.com",
                    proxy_routes=("*.example.com=orchestrator:8090",),
                )

    def test_compose_proxy_route_upstream_must_be_in_selected_subset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n"
                "  mail:\n"
                "    image: example/mail:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                    auth_token="TokenABC123",
                    compose_services=("orchestrator",),
                    proxy_routes=("mail.example.com=mail:4000",),
                )


if __name__ == "__main__":
    unittest.main()
