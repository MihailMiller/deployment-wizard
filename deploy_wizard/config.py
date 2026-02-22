"""
Immutable configuration for generic Docker microservice deployment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class SourceKind(str, Enum):
    AUTO = "auto"
    COMPOSE = "compose"
    DOCKERFILE = "dockerfile"


_SERVICE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def find_compose_file(source_dir: Path) -> Optional[Path]:
    candidates = (
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    )
    for name in candidates:
        path = source_dir / name
        if path.exists() and path.is_file():
            return path
    return None


def detect_source_kind(source_dir: Path) -> SourceKind:
    compose_path = find_compose_file(source_dir)
    dockerfile_path = source_dir / "Dockerfile"
    if compose_path is not None:
        return SourceKind.COMPOSE
    if dockerfile_path.exists() and dockerfile_path.is_file():
        return SourceKind.DOCKERFILE
    raise ValueError(
        f"{source_dir} does not contain docker-compose.yml/compose.yml or Dockerfile."
    )


@dataclass(frozen=True)
class Config:
    service_name: str
    source_dir: Path
    source_kind: SourceKind = SourceKind.AUTO
    base_dir: Path = Path("/opt/services")
    host_port: Optional[int] = None
    container_port: Optional[int] = None
    bind_host: str = "127.0.0.1"
    registry_retries: int = 4
    retry_backoff_seconds: int = 5
    tune_docker_daemon: bool = True

    def __post_init__(self) -> None:
        if not _SERVICE_NAME_RE.fullmatch(self.service_name):
            raise ValueError(
                f"service_name={self.service_name!r} is invalid. "
                "Use letters, numbers, '.', '_', '-'."
            )
        if not self.source_dir.exists() or not self.source_dir.is_dir():
            raise ValueError(f"source_dir={self.source_dir!s} must be an existing directory.")

        resolved_kind = self.source_kind
        if resolved_kind == SourceKind.AUTO:
            resolved_kind = detect_source_kind(self.source_dir)
            object.__setattr__(self, "source_kind", resolved_kind)

        if resolved_kind == SourceKind.COMPOSE and self.source_compose_path is None:
            raise ValueError("source_kind=compose requires a compose file in source_dir.")

        if resolved_kind == SourceKind.DOCKERFILE and not self.source_dockerfile_path.exists():
            raise ValueError("source_kind=dockerfile requires source_dir/Dockerfile.")

        has_host = self.host_port is not None
        has_container = self.container_port is not None
        if has_host != has_container:
            raise ValueError("host_port and container_port must be set together.")

        for name, port in (
            ("host_port", self.host_port),
            ("container_port", self.container_port),
        ):
            if port is not None and not (1 <= int(port) <= 65535):
                raise ValueError(f"{name}={port} must be between 1 and 65535.")

        if not self.bind_host.strip():
            raise ValueError("bind_host must not be empty.")

        if self.registry_retries < 1:
            raise ValueError("registry_retries must be >= 1.")
        if self.retry_backoff_seconds < 1:
            raise ValueError("retry_backoff_seconds must be >= 1.")

    @property
    def service_dir(self) -> Path:
        return self.base_dir / self.service_name

    @property
    def compose_project_name(self) -> str:
        # Docker Compose project names are lowercase and limited charset.
        normalized = re.sub(r"[^a-z0-9_-]", "-", self.service_name.lower())
        normalized = normalized.strip("-_")
        return normalized or "service"

    @property
    def service_key(self) -> str:
        return self.compose_project_name

    @property
    def source_compose_path(self) -> Optional[Path]:
        return find_compose_file(self.source_dir)

    @property
    def source_dockerfile_path(self) -> Path:
        return self.source_dir / "Dockerfile"

    @property
    def managed_compose_path(self) -> Path:
        return self.service_dir / "docker-compose.generated.yml"
