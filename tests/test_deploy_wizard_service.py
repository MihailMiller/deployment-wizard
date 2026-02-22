import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy_wizard.config import AccessMode, Config, SourceKind
from deploy_wizard.service import (
    _run_with_retries,
    deploy_compose_source,
    deploy_dockerfile_source,
    write_generated_compose,
    write_nginx_proxy_config,
    write_proxy_compose,
)


class DeployWizardServiceTests(unittest.TestCase):
    def test_generated_compose_without_ports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
            )
            write_generated_compose(cfg)
            content = cfg.managed_compose_path.read_text(encoding="utf-8")
            self.assertIn("services:", content)
            self.assertIn("demo:", content)
            self.assertIn("dockerfile: Dockerfile", content)
            self.assertNotIn("ports:", content)

    def test_generated_compose_with_ports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
                host_port=18080,
                container_port=8080,
                bind_host="127.0.0.1",
            )
            write_generated_compose(cfg)
            content = cfg.managed_compose_path.read_text(encoding="utf-8")
            self.assertIn("ports:", content)
            self.assertIn('"127.0.0.1:18080:8080"', content)

    def test_generated_compose_with_public_access_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
                host_port=18080,
                container_port=8080,
                access_mode=AccessMode.PUBLIC,
            )
            write_generated_compose(cfg)
            content = cfg.managed_compose_path.read_text(encoding="utf-8")
            self.assertIn('"0.0.0.0:18080:8080"', content)

    def test_run_with_retries_eventual_success(self) -> None:
        with mock.patch("deploy_wizard.service.sh", side_effect=[1, 0]) as sh_mock, \
             mock.patch("deploy_wizard.service.log_line"), \
             mock.patch("deploy_wizard.service.time.sleep") as sleep_mock:
            ok = _run_with_retries(
                "docker compose up -d --build",
                attempts=3,
                backoff_seconds=2,
                context="compose deploy",
            )
        self.assertTrue(ok)
        self.assertEqual(sh_mock.call_count, 2)
        sleep_mock.assert_called_once_with(2)

    def test_run_with_retries_exhausted(self) -> None:
        with mock.patch("deploy_wizard.service.sh", return_value=1) as sh_mock, \
             mock.patch("deploy_wizard.service.log_line"), \
             mock.patch("deploy_wizard.service.time.sleep") as sleep_mock:
            ok = _run_with_retries(
                "docker compose up -d --build",
                attempts=3,
                backoff_seconds=2,
                context="compose deploy",
            )
        self.assertFalse(ok)
        self.assertEqual(sh_mock.call_count, 3)
        # attempts=3 means 2 sleeps between attempts
        self.assertEqual(sleep_mock.call_count, 2)

    def test_deploy_compose_source_with_selected_services(self) -> None:
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
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.COMPOSE,
                compose_services=("api", "worker"),
            )
            with mock.patch("deploy_wizard.service._run_with_retries", return_value=True) as run_mock:
                deploy_compose_source(cfg)

        cmd = run_mock.call_args[0][0]
        self.assertIn(" up -d --build api worker", cmd)

    def test_write_proxy_compose_and_nginx_conf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
                container_port=8080,
                host_port=18080,
                access_mode=AccessMode.PUBLIC,
                domain="api.example.com",
                certbot_email="ops@example.com",
            )
            write_generated_compose(cfg)
            write_proxy_compose(cfg)
            write_nginx_proxy_config(cfg, https_enabled=False)
            proxy_content = cfg.managed_proxy_compose_path.read_text(encoding="utf-8")
            nginx_content = cfg.managed_nginx_conf_path.read_text(encoding="utf-8")
            self.assertIn("nginx:", proxy_content)
            self.assertIn("certbot:", proxy_content)
            self.assertIn("server_name api.example.com;", nginx_content)
            self.assertIn("proxy_pass http://demo:8080;", nginx_content)

    def test_write_proxy_compose_and_nginx_conf_with_auth_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
                container_port=8080,
                host_port=18080,
                access_mode=AccessMode.PUBLIC,
                auth_token="TokenABC123",
            )
            write_generated_compose(cfg)
            write_proxy_compose(cfg)
            write_nginx_proxy_config(cfg, https_enabled=False)
            proxy_content = cfg.managed_proxy_compose_path.read_text(encoding="utf-8")
            nginx_content = cfg.managed_nginx_conf_path.read_text(encoding="utf-8")
            self.assertIn('"0.0.0.0:80:80"', proxy_content)
            self.assertNotIn("certbot:", proxy_content)
            self.assertIn('if ($http_authorization != "Bearer TokenABC123") {', nginx_content)

    def test_deploy_dockerfile_source_with_tls_runs_certbot_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = Config(
                service_name="demo",
                source_dir=src,
                source_kind=SourceKind.DOCKERFILE,
                base_dir=Path(td) / "services",
                container_port=8080,
                host_port=18080,
                access_mode=AccessMode.PUBLIC,
                domain="api.example.com",
                certbot_email="ops@example.com",
            )
            with mock.patch("deploy_wizard.service._run_with_retries", return_value=True) as run_mock, \
                 mock.patch("deploy_wizard.service._issue_certificate") as cert_mock, \
                 mock.patch("deploy_wizard.service._reload_nginx") as reload_mock:
                deploy_dockerfile_source(cfg)

        cmd = run_mock.call_args[0][0]
        self.assertIn(" up -d --build demo nginx", cmd)
        cert_mock.assert_called_once()
        reload_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
