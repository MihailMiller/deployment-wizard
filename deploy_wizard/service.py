"""
Service deployment logic for compose-backed and Dockerfile-backed sources.
"""

from __future__ import annotations

from pathlib import Path
from shlex import quote

from deploy_wizard.config import Config, SourceKind
from deploy_wizard.log import sh


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_generated_compose(cfg: Config) -> None:
    ports_block = ""
    if cfg.host_port is not None and cfg.container_port is not None:
        ports_block = (
            "    ports:\n"
            f'      - "{cfg.bind_host}:{cfg.host_port}:{cfg.container_port}"\n'
        )
    content = (
        "services:\n"
        f"  {cfg.service_key}:\n"
        "    build:\n"
        f"      context: {cfg.source_dir}\n"
        "      dockerfile: Dockerfile\n"
        f"    image: {cfg.compose_project_name}:local\n"
        f"    container_name: {cfg.compose_project_name}\n"
        "    restart: unless-stopped\n"
        f"{ports_block}"
    )
    write_file(cfg.managed_compose_path, content)


def deploy_compose_source(cfg: Config) -> None:
    compose_path = cfg.source_compose_path
    if compose_path is None:
        raise ValueError("Compose source deployment requires a compose file.")
    sh(
        f"cd {quote(str(cfg.source_dir))} && "
        f"docker compose -p {quote(cfg.compose_project_name)} "
        f"-f {quote(str(compose_path))} up -d --build"
    )


def deploy_dockerfile_source(cfg: Config) -> None:
    write_generated_compose(cfg)
    sh(
        f"cd {quote(str(cfg.service_dir))} && "
        f"docker compose -p {quote(cfg.compose_project_name)} "
        f"-f {quote(str(cfg.managed_compose_path))} up -d --build"
    )


def deploy_service(cfg: Config) -> None:
    if cfg.source_kind == SourceKind.COMPOSE:
        deploy_compose_source(cfg)
        return
    if cfg.source_kind == SourceKind.DOCKERFILE:
        deploy_dockerfile_source(cfg)
        return
    raise ValueError(f"Unsupported source kind: {cfg.source_kind}")
