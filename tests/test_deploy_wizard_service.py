import tempfile
import unittest
from pathlib import Path

from deploy_wizard.config import Config, SourceKind
from deploy_wizard.service import write_generated_compose


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


if __name__ == "__main__":
    unittest.main()
