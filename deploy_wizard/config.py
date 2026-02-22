"""
Immutable configuration for generic Docker microservice deployment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple


class SourceKind(str, Enum):
    AUTO = "auto"
    COMPOSE = "compose"
    DOCKERFILE = "dockerfile"


class AccessMode(str, Enum):
    LOCALHOST = "localhost"
    TAILSCALE = "tailscale"
    PUBLIC = "public"


_SERVICE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+\-]{8,}$")


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


def list_compose_services(compose_path: Path) -> List[str]:
    """
    Best-effort parser for top-level `services:` keys in a compose YAML file.
    """
    if not compose_path.exists() or not compose_path.is_file():
        return []

    services_indent: Optional[int] = None
    child_indent: Optional[int] = None
    names: List[str] = []
    key_pattern = re.compile(
        r'^(\s*)(?:'
        r'"([^"]+)"|'
        r"'([^']+)'|"
        r"([A-Za-z0-9_.-]+)"
        r')\s*:\s*(?:$|#)'
    )

    for raw_line in compose_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        if services_indent is None:
            services_match = re.match(r"^(\s*)services\s*:\s*(?:$|#)", line)
            if services_match is not None:
                services_indent = len(services_match.group(1))
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent <= services_indent:
            break

        key_match = key_pattern.match(raw_line)
        if key_match is None:
            continue

        key_indent = len(key_match.group(1))
        if child_indent is None:
            child_indent = key_indent
        if key_indent != child_indent:
            continue

        name = key_match.group(2) or key_match.group(3) or key_match.group(4) or ""
        if name and name not in names:
            names.append(name)

    return names


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
    access_mode: AccessMode = AccessMode.LOCALHOST
    registry_retries: int = 4
    retry_backoff_seconds: int = 5
    tune_docker_daemon: bool = True
    compose_services: Optional[Tuple[str, ...]] = None
    domain: Optional[str] = None
    certbot_email: Optional[str] = None
    auth_token: Optional[str] = None
    proxy_upstream_service: Optional[str] = None
    proxy_upstream_port: Optional[int] = None

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
        if resolved_kind == SourceKind.DOCKERFILE and self.compose_services:
            raise ValueError("compose_services can only be set for compose sources.")

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

        if self.compose_services is not None:
            normalized: List[str] = []
            for service in self.compose_services:
                name = str(service).strip()
                if not name:
                    raise ValueError("compose_services must not contain empty names.")
                if name not in normalized:
                    normalized.append(name)
            object.__setattr__(self, "compose_services", tuple(normalized))

            if resolved_kind == SourceKind.COMPOSE and self.source_compose_path is not None:
                known_services = set(list_compose_services(self.source_compose_path))
                if known_services:
                    unknown = [s for s in normalized if s not in known_services]
                    if unknown:
                        raise ValueError(
                            "Unknown compose service(s): "
                            + ", ".join(unknown)
                            + ". Available: "
                            + ", ".join(sorted(known_services))
                        )

        domain = str(self.domain).strip().lower() if self.domain is not None else None
        certbot_email = (
            str(self.certbot_email).strip().lower()
            if self.certbot_email is not None
            else None
        )
        auth_token = (
            str(self.auth_token).strip()
            if self.auth_token is not None
            else None
        )
        proxy_upstream_service = (
            str(self.proxy_upstream_service).strip()
            if self.proxy_upstream_service is not None
            else None
        )

        if domain is not None:
            object.__setattr__(self, "domain", domain)
        if certbot_email is not None:
            object.__setattr__(self, "certbot_email", certbot_email)
        if auth_token is not None:
            object.__setattr__(self, "auth_token", auth_token)
        if proxy_upstream_service is not None:
            object.__setattr__(self, "proxy_upstream_service", proxy_upstream_service)

        if self.proxy_upstream_port is not None and not (
            1 <= int(self.proxy_upstream_port) <= 65535
        ):
            raise ValueError("proxy_upstream_port must be between 1 and 65535.")

        if self.auth_token is not None and not _TOKEN_RE.fullmatch(self.auth_token):
            raise ValueError(
                "auth_token must be >= 8 chars and only contain [A-Za-z0-9._~+-]."
            )

        if (
            resolved_kind == SourceKind.COMPOSE
            and self.access_mode != AccessMode.LOCALHOST
            and not self.reverse_proxy_enabled
        ):
            raise ValueError(
                "access_mode for compose sources requires domain or auth_token "
                "(managed proxy mode)."
            )

        if self.tls_enabled:
            if self.domain is None or not _DOMAIN_RE.fullmatch(self.domain):
                raise ValueError("domain must be a valid DNS name, e.g. api.example.com.")
            if self.certbot_email is None or not _EMAIL_RE.fullmatch(self.certbot_email):
                raise ValueError("certbot_email must be a valid email address.")
            if self.access_mode != AccessMode.PUBLIC:
                raise ValueError(
                    "domain/certbot mode requires access_mode=public "
                    "for HTTP-01 challenge reachability."
                )
        else:
            if self.certbot_email is not None:
                raise ValueError("certbot_email requires domain.")

        if self.reverse_proxy_enabled:
            if resolved_kind == SourceKind.DOCKERFILE and self.proxy_upstream_service:
                raise ValueError(
                    "proxy_upstream_service is only supported for compose sources."
                )
            if self.proxy_upstream_service and not _SERVICE_NAME_RE.fullmatch(
                self.proxy_upstream_service
            ):
                raise ValueError(
                    "proxy_upstream_service is invalid. "
                    "Use letters, numbers, '.', '_', '-'."
                )
            if resolved_kind == SourceKind.COMPOSE and self.proxy_upstream_service:
                known = set(list_compose_services(self.source_compose_path or Path("/__none__")))
                if known and self.proxy_upstream_service not in known:
                    raise ValueError(
                        "proxy_upstream_service must be one of: "
                        + ", ".join(sorted(known))
                    )
                if (
                    self.compose_services
                    and self.proxy_upstream_service not in self.compose_services
                ):
                    raise ValueError(
                        "proxy_upstream_service must be included in compose_services."
                    )
            _ = self.effective_proxy_upstream_service
            _ = self.effective_proxy_upstream_port
        else:
            if (
                self.auth_token is not None
                or self.proxy_upstream_service is not None
                or self.proxy_upstream_port is not None
            ):
                raise ValueError(
                    "auth/proxy settings require domain or auth_token to enable proxy mode."
                )

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

    @property
    def managed_proxy_compose_path(self) -> Path:
        return self.service_dir / "docker-compose.proxy.yml"

    @property
    def managed_nginx_conf_path(self) -> Path:
        return self.service_dir / "nginx" / "default.conf"

    @property
    def tls_enabled(self) -> bool:
        return self.domain is not None

    @property
    def reverse_proxy_enabled(self) -> bool:
        return self.tls_enabled or self.auth_token is not None

    @property
    def effective_bind_host(self) -> str:
        if self.access_mode == AccessMode.PUBLIC:
            return "0.0.0.0"
        return self.bind_host

    @property
    def effective_proxy_upstream_service(self) -> str:
        if not self.reverse_proxy_enabled:
            raise ValueError("No upstream service without proxy mode.")
        if self.source_kind == SourceKind.DOCKERFILE:
            return self.service_key
        if self.proxy_upstream_service:
            return self.proxy_upstream_service
        if self.compose_services:
            return self.compose_services[0]
        compose_path = self.source_compose_path
        if compose_path is not None:
            discovered = list_compose_services(compose_path)
            if discovered:
                return discovered[0]
        raise ValueError(
            "Could not infer compose upstream service. "
            "Set proxy_upstream_service explicitly."
        )

    @property
    def effective_proxy_upstream_port(self) -> int:
        if not self.reverse_proxy_enabled:
            raise ValueError("No upstream port without proxy mode.")
        if self.proxy_upstream_port is not None:
            return int(self.proxy_upstream_port)
        if self.container_port is not None:
            return int(self.container_port)
        raise ValueError(
            "Proxy mode requires proxy_upstream_port "
            "(or container_port for dockerfile sources)."
        )
