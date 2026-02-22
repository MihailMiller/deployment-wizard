import tempfile
import unittest
from pathlib import Path

from deploy_wizard.config import (
    AccessMode,
    Config,
    IngressMode,
    SourceKind,
    list_compose_required_env_vars,
    list_missing_compose_env_vars,
    read_dotenv_values,
    list_compose_service_host_ports,
    list_compose_service_ports,
    list_compose_services,
)


class DeployWizardConfigTests(unittest.TestCase):
    def test_list_compose_required_env_vars_detects_required_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            compose = src / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  api:\n"
                "    image: ${IMAGE_NAME}\n"
                "    environment:\n"
                "      - MAYBE=$MAYBE\n"
                "      - OPTIONAL_A=${HF_TOKEN:-}\n"
                "      - OPTIONAL_B=${WITH_DEFAULT-default}\n"
                "      - STRICT=${AUTH_TOKEN:?auth required}\n",
                encoding="utf-8",
            )
            self.assertEqual(
                list_compose_required_env_vars(compose),
                (
                    ("IMAGE_NAME", False),
                    ("MAYBE", False),
                    ("AUTH_TOKEN", True),
                ),
            )

    def test_list_missing_compose_env_vars_respects_dotenv_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            compose = src / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  llm:\n"
                "    image: ${LLM_IMAGE}\n"
                "    environment:\n"
                "      - HF_TOKEN=${HF_TOKEN:-}\n"
                "      - AUTH_TOKEN=${AUTH_TOKEN:?required}\n",
                encoding="utf-8",
            )
            (src / ".env").write_text(
                "LLM_IMAGE=ghcr.io/example/llm:latest\n"
                "AUTH_TOKEN=\n",
                encoding="utf-8",
            )
            missing = list_missing_compose_env_vars(
                compose,
                dotenv_path=src / ".env",
                env={},
            )
            self.assertEqual(missing, (("AUTH_TOKEN", True),))
            missing_with_env = list_missing_compose_env_vars(
                compose,
                dotenv_path=src / ".env",
                env={"AUTH_TOKEN": "TokenABC123"},
            )
            self.assertEqual(missing_with_env, tuple())

    def test_read_dotenv_values_supports_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / ".env"
            env_path.write_text(
                "PLAIN=value\n"
                "export QUOTED=\"space value\"\n"
                "SINGLE='abc'\n"
                "# comment\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_dotenv_values(env_path),
                {
                    "PLAIN": "value",
                    "QUOTED": "space value",
                    "SINGLE": "abc",
                },
            )

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

    def test_list_compose_service_ports_discovers_ports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            compose = src / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  workflow-studio:\n"
                "    image: example/workflow-studio:latest\n"
                "    ports:\n"
                '      - "8000:8000"\n'
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n"
                "    ports:\n"
                '      - "127.0.0.1:8080:8080"\n'
                "  logbook:\n"
                "    image: example/logbook:latest\n"
                "    expose:\n"
                '      - "8010"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                list_compose_service_ports(compose),
                {
                    "workflow-studio": 8000,
                    "orchestrator": 8080,
                    "logbook": 8010,
                },
            )
            self.assertEqual(
                list_compose_service_ports(compose, include_expose=False),
                {
                    "workflow-studio": 8000,
                    "orchestrator": 8080,
                },
            )
            self.assertEqual(
                list_compose_service_host_ports(compose),
                {
                    "workflow-studio": 8000,
                    "orchestrator": 8080,
                },
            )

    def test_list_compose_service_ports_handles_extensions_and_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            compose = src / "docker-compose.yml"
            compose.write_text(
                "x-logging: &default-logging\n"
                "  driver: json-file\n"
                "  options:\n"
                '    max-size: "10m"\n'
                '    max-file: "3"\n'
                "\n"
                "services:\n"
                "  orchestrator:\n"
                "    build: ./orchestrator\n"
                "    ports:\n"
                '      - "127.0.0.1:8080:8080"\n'
                "    logging: *default-logging\n"
                "  workflow-studio:\n"
                "    build:\n"
                "      context: ./apps\n"
                "      dockerfile: workflow-studio/Dockerfile\n"
                "    ports:\n"
                '      - "127.0.0.1:8000:8000"\n'
                "    logging: *default-logging\n"
                "  stt:\n"
                "    build: ./services/stt\n"
                "    expose:\n"
                '      - "10300"\n'
                "    logging: *default-logging\n",
                encoding="utf-8",
            )
            self.assertEqual(
                list_compose_services(compose),
                ["orchestrator", "workflow-studio", "stt"],
            )
            self.assertEqual(
                list_compose_service_ports(compose),
                {
                    "orchestrator": 8080,
                    "workflow-studio": 8000,
                    "stt": 10300,
                },
            )
            self.assertEqual(
                list_compose_service_ports(compose, include_expose=False),
                {
                    "orchestrator": 8080,
                    "workflow-studio": 8000,
                },
            )

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

    def test_proxy_routes_support_host_path_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n"
                "  logbook:\n"
                "    image: example/logbook:latest\n",
                encoding="utf-8",
            )
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.COMPOSE,
                access_mode=AccessMode.PUBLIC,
                domain="apps.example.com",
                certbot_email="ops@example.com",
                proxy_routes=(
                    "apps.example.com/orchestrator=orchestrator:8080",
                    "apps.example.com/logbook=logbook:8010",
                ),
            )
            self.assertEqual(len(cfg.effective_proxy_routes), 2)
            self.assertEqual(cfg.effective_proxy_routes[0].path_prefix, "/orchestrator")
            self.assertEqual(cfg.effective_proxy_routes[1].path_prefix, "/logbook")
            self.assertEqual(cfg.cert_domain_names, ("apps.example.com",))

    def test_proxy_routes_reject_duplicate_host_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  orchestrator:\n"
                "    image: example/orchestrator:latest\n"
                "  logbook:\n"
                "    image: example/logbook:latest\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                Config(
                    service_name="svc",
                    source_dir=src,
                    source_kind=SourceKind.COMPOSE,
                    access_mode=AccessMode.PUBLIC,
                    auth_token="TokenABC123",
                    proxy_routes=(
                        "apps.example.com/orchestrator=orchestrator:8080",
                        "apps.example.com/orchestrator=logbook:8010",
                    ),
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

    def test_external_nginx_compose_requires_routes(self) -> None:
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
                    ingress_mode=IngressMode.EXTERNAL_NGINX,
                    auth_token="TokenABC123",
                )

    def test_external_nginx_dockerfile_uses_localhost_host_port_route(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="svc",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                access_mode=AccessMode.PUBLIC,
                ingress_mode=IngressMode.EXTERNAL_NGINX,
                host_port=18080,
                container_port=8080,
                auth_token="TokenABC123",
            )
            route = cfg.effective_proxy_routes[0]
            self.assertEqual(route.upstream_host, "127.0.0.1")
            self.assertEqual(route.upstream_port, 18080)

    def test_external_nginx_compose_rejects_service_name_upstream_routes(self) -> None:
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
                    ingress_mode=IngressMode.EXTERNAL_NGINX,
                    auth_token="TokenABC123",
                    proxy_routes=("api.example.com=orchestrator:8080",),
                )


if __name__ == "__main__":
    unittest.main()
