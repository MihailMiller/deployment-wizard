"""
Service deployment logic for compose-backed and Dockerfile-backed sources.
"""

from __future__ import annotations

import time
from pathlib import Path
from shlex import quote

from deploy_wizard.config import Config, SourceKind
from deploy_wizard.log import die, log_line, sh


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


def _run_with_retries(
    cmd: str,
    *,
    attempts: int,
    backoff_seconds: int,
    context: str,
) -> bool:
    """
    Retry transient docker/registry failures with exponential backoff.
    """
    for attempt in range(1, attempts + 1):
        rc = sh(cmd, check=False)
        if rc == 0:
            return True
        if attempt == attempts:
            break
        delay = backoff_seconds * (2 ** (attempt - 1))
        msg = (
            f"[RETRY] {context} failed (attempt {attempt}/{attempts}, exit={rc}). "
            f"Retrying in {delay}s..."
        )
        print(msg, flush=True)
        log_line(msg)
        time.sleep(delay)
    return False


def deploy_compose_source(cfg: Config) -> None:
    compose_path = cfg.source_compose_path
    if compose_path is None:
        raise ValueError("Compose source deployment requires a compose file.")
    cmd = (
        f"cd {quote(str(cfg.source_dir))} && "
        f"docker compose -p {quote(cfg.compose_project_name)} "
        f"-f {quote(str(compose_path))} up -d --build"
    )
    if not _run_with_retries(
        cmd,
        attempts=cfg.registry_retries,
        backoff_seconds=cfg.retry_backoff_seconds,
        context="compose deploy",
    ):
        die(
            "Docker compose deploy failed after retries. "
            "This is usually caused by registry/network instability."
        )


def deploy_dockerfile_source(cfg: Config) -> None:
    write_generated_compose(cfg)
    cmd = (
        f"cd {quote(str(cfg.service_dir))} && "
        f"docker compose -p {quote(cfg.compose_project_name)} "
        f"-f {quote(str(cfg.managed_compose_path))} up -d --build"
    )
    if not _run_with_retries(
        cmd,
        attempts=cfg.registry_retries,
        backoff_seconds=cfg.retry_backoff_seconds,
        context="dockerfile deploy",
    ):
        die(
            "Docker compose build/deploy failed after retries. "
            "This is usually caused by registry/network instability."
        )


def deploy_service(cfg: Config) -> None:
    if cfg.source_kind == SourceKind.COMPOSE:
        deploy_compose_source(cfg)
        return
    if cfg.source_kind == SourceKind.DOCKERFILE:
        deploy_dockerfile_source(cfg)
        return
    raise ValueError(f"Unsupported source kind: {cfg.source_kind}")
