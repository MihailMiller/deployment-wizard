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
            (src / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            cfg = build_config(
                [
                    "--service-name",
                    "demo",
                    "--source-dir",
                    str(src),
                ]
            )
            self.assertEqual(cfg.source_kind, SourceKind.COMPOSE)
            self.assertEqual(cfg.service_name, "demo")

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


if __name__ == "__main__":
    unittest.main()
