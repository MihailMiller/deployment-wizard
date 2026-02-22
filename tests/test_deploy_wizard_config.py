import tempfile
import unittest
from pathlib import Path

from deploy_wizard.config import Config, SourceKind, list_compose_services


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


if __name__ == "__main__":
    unittest.main()
