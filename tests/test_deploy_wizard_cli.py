import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy_wizard.cli import build_config
from deploy_wizard.config import SourceKind


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeployWizardCliTests(unittest.TestCase):
    def test_build_config_with_compose_source(self) -> None:
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
            cfg = build_config(
                [
                    "--service-name",
                    "demo",
                    "--source-dir",
                    str(src),
                    "--compose-service",
                    "api",
                    "--compose-service",
                    "worker",
                ]
            )
            self.assertEqual(cfg.source_kind, SourceKind.COMPOSE)
            self.assertEqual(cfg.service_name, "demo")
            self.assertEqual(cfg.compose_services, ("api", "worker"))

    def test_build_config_with_dockerfile_ports(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = build_config(
                [
                    "--service-name",
                    "demo",
                    "--source-dir",
                    str(src),
                    "--source-kind",
                    "dockerfile",
                    "--host-port",
                    "18080",
                    "--container-port",
                    "8080",
                ]
            )
            self.assertEqual(cfg.source_kind, SourceKind.DOCKERFILE)
            self.assertEqual(cfg.host_port, 18080)
            self.assertEqual(cfg.container_port, 8080)

    def test_build_config_with_tls_proxy_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "docker-compose.yml").write_text(
                "services:\n"
                "  api:\n"
                "    image: example/api:latest\n",
                encoding="utf-8",
            )
            cfg = build_config(
                [
                    "--service-name",
                    "demo",
                    "--source-dir",
                    str(src),
                    "--domain",
                    "api.example.com",
                    "--access-mode",
                    "public",
                    "--certbot-email",
                    "ops@example.com",
                    "--proxy-upstream-service",
                    "api",
                    "--proxy-upstream-port",
                    "8080",
                ]
            )
            self.assertEqual(cfg.domain, "api.example.com")
            self.assertEqual(cfg.certbot_email, "ops@example.com")
            self.assertEqual(cfg.effective_proxy_upstream_service, "api")
            self.assertEqual(cfg.effective_proxy_upstream_port, 8080)

    def test_build_config_with_auth_token_and_access_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "Dockerfile").write_text("FROM alpine:3.20\n", encoding="utf-8")
            cfg = build_config(
                [
                    "--service-name",
                    "demo",
                    "--source-dir",
                    str(src),
                    "--source-kind",
                    "dockerfile",
                    "--host-port",
                    "18080",
                    "--container-port",
                    "8080",
                    "--auth-token",
                    "TokenABC123",
                    "--access-mode",
                    "public",
                ]
            )
            self.assertEqual(cfg.access_mode.value, "public")
            self.assertEqual(cfg.auth_token, "TokenABC123")
            self.assertTrue(cfg.reverse_proxy_enabled)

    def test_batch_help_prints_expected_flags(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "deploy_wizard", "deploy", "--batch", "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("--service-name", proc.stdout)
        self.assertIn("--source-dir", proc.stdout)
        self.assertIn("--registry-retries", proc.stdout)
        self.assertIn("--compose-service", proc.stdout)
        self.assertIn("--domain", proc.stdout)
        self.assertIn("--certbot-email", proc.stdout)
        self.assertIn("--auth-token", proc.stdout)
        self.assertIn("--access-mode", proc.stdout)


if __name__ == "__main__":
    unittest.main()
