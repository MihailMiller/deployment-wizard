"""
Immutable configuration for generic Docker microservice deployment.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


class SourceKind(str, Enum):
    AUTO = "auto"
    COMPOSE = "compose"
    DOCKERFILE = "dockerfile"


class AccessMode(str, Enum):
    LOCALHOST = "localhost"
    TAILSCALE = "tailscale"
    PUBLIC = "public"


class IngressMode(str, Enum):
    MANAGED = "managed"
    EXTERNAL_NGINX = "external-nginx"
    TAKEOVER = "takeover"


_SERVICE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+\-]{8,}$")
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9*_.-]+$")
_UPSTREAM_HOST_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/\-]*$")
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ProxyRoute:
    host: str
    upstream_host: str
    upstream_port: int
    path_prefix: str = "/"


def _normalize_path_prefix(raw: str) -> str:
    text = str(raw).strip()
    if not text:
        return "/"
    if not text.startswith("/"):
        text = "/" + text
    text = re.sub(r"/+", "/", text)
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    if " " in text or not _PATH_PREFIX_RE.fullmatch(text):
        raise ValueError(
            "proxy_route path is invalid. "
            "Use URL path prefixes like /service or /api/v1."
        )
    return text


def parse_proxy_route(raw: str) -> ProxyRoute:
    text = str(raw).strip()
    if not text:
        raise ValueError("proxy_route must not be empty.")
    if "=" not in text:
        raise ValueError(
            "proxy_route format must be '<host>[/path]=<upstream-host>:<port>'."
        )
    host_part, target_part = text.split("=", 1)
    host_field = host_part.strip().lower()
    if "/" in host_field:
        host_token, path_token = host_field.split("/", 1)
        host = host_token.strip()
        path_prefix = _normalize_path_prefix(path_token)
    else:
        host = host_field
        path_prefix = "/"
    target = target_part.strip()
    if ":" not in target:
        raise ValueError(
            "proxy_route target must include port, e.g. api:8080."
        )
    upstream_host, port_raw = target.rsplit(":", 1)
    upstream_host = upstream_host.strip()
    port_text = port_raw.strip()

    if not host:
        raise ValueError("proxy_route host must not be empty.")
    if not _SERVER_NAME_RE.fullmatch(host):
        raise ValueError(
            "proxy_route host is invalid. "
            "Use a hostname/wildcard server_name like app.example.com."
        )
    if not upstream_host or not _UPSTREAM_HOST_RE.fullmatch(upstream_host):
        raise ValueError(
            "proxy_route upstream host is invalid. "
            "Use letters, numbers, '.', '_', '-'."
        )
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("proxy_route upstream port must be an integer.") from exc
    if not (1 <= port <= 65535):
        raise ValueError("proxy_route upstream port must be between 1 and 65535.")

    return ProxyRoute(
        host=host,
        upstream_host=upstream_host,
        upstream_port=port,
        path_prefix=path_prefix,
    )


def _merge_env_requirement(
    name: str,
    level: int,
    *,
    order: List[str],
    levels: Dict[str, int],
) -> None:
    if not _ENV_VAR_NAME_RE.fullmatch(name):
        return
    if name not in levels:
        order.append(name)
        levels[name] = level
        return
    levels[name] = max(levels[name], level)


def _parse_braced_env_requirement(expr: str) -> Optional[Tuple[str, int]]:
    text = str(expr).strip()
    if not text:
        return None
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?]).*)?$", text)
    if match is None:
        return None
    name = match.group(1)
    op = match.group(2)
    if op in ("-", ":-", "+", ":+"):
        # Has default/alternative; no value is required.
        return None
    if op == ":?":
        return (name, 2)
    return (name, 1)


def list_compose_required_env_vars(compose_path: Path) -> Tuple[Tuple[str, bool], ...]:
    """
    Discover interpolation variables in compose files that require user-provided values.

    Returns tuples of (NAME, require_non_empty), preserving first-seen order.
    """
    if not compose_path.exists() or not compose_path.is_file():
        return tuple()

    content = compose_path.read_text(encoding="utf-8")
    required_order: List[str] = []
    required_levels: Dict[str, int] = {}
    idx = 0

    while idx < len(content):
        if content[idx] != "$":
            idx += 1
            continue
        if idx + 1 >= len(content):
            break

        nxt = content[idx + 1]
        if nxt == "$":
            # Escaped dollar sign (literal).
            idx += 2
            continue
        if nxt == "{":
            end = content.find("}", idx + 2)
            if end < 0:
                idx += 1
                continue
            parsed = _parse_braced_env_requirement(content[idx + 2:end])
            if parsed is not None:
                name, level = parsed
                _merge_env_requirement(
                    name,
                    level,
                    order=required_order,
                    levels=required_levels,
                )
            idx = end + 1
            continue

        name_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", content[idx + 1 :])
        if name_match is not None:
            _merge_env_requirement(
                name_match.group(0),
                1,
                order=required_order,
                levels=required_levels,
            )
            idx += 1 + len(name_match.group(0))
            continue

        idx += 1

    return tuple((name, required_levels[name] >= 2) for name in required_order)


def read_dotenv_values(dotenv_path: Path) -> Dict[str, str]:
    """
    Read KEY=VALUE entries from a .env-like file.
    """
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return {}

    values: Dict[str, str] = {}
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key_raw, value_raw = line.split("=", 1)
        key = key_raw.strip()
        if not _ENV_VAR_NAME_RE.fullmatch(key):
            continue
        value = value_raw.strip()
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        values[key] = value
    return values


def list_missing_compose_env_vars(
    compose_path: Path,
    *,
    dotenv_path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Tuple[str, bool], ...]:
    """
    Return required compose variables that are unset/empty.
    """
    required = list_compose_required_env_vars(compose_path)
    if not required:
        return tuple()

    merged: Dict[str, str] = {}
    if dotenv_path is not None:
        merged.update(read_dotenv_values(dotenv_path))
    source_env = os.environ if env is None else env
    merged.update({str(k): str(v) for k, v in source_env.items()})

    missing: List[Tuple[str, bool]] = []
    for name, require_non_empty in required:
        value = merged.get(name)
        if value is None or value == "":
            missing.append((name, require_non_empty))
    return tuple(missing)


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


def _parse_port_mapping(token: str) -> Tuple[Optional[int], Optional[int]]:
    text = str(token).strip().strip("'").strip('"')
    if not text:
        return None, None
    text = text.split("/", 1)[0].strip()
    parts = [part.strip() for part in text.split(":")]

    def _to_port(raw: str) -> Optional[int]:
        if not raw or not raw.isdigit():
            return None
        value = int(raw)
        if 1 <= value <= 65535:
            return value
        return None

    if len(parts) == 1:
        return None, _to_port(parts[0])
    if len(parts) == 2:
        return _to_port(parts[0]), _to_port(parts[1])
    return _to_port(parts[-2]), _to_port(parts[-1])


def _extract_container_port(token: str) -> Optional[int]:
    _host_port, container_port = _parse_port_mapping(token)
    return container_port


def _extract_host_port(token: str) -> Optional[int]:
    host_port, _container_port = _parse_port_mapping(token)
    return host_port


def list_compose_service_ports(
    compose_path: Path,
    *,
    include_expose: bool = True,
) -> dict:
    """
    Best-effort parser for first exposed/container port per compose service.
    Supports common list forms in `ports:` and `expose:`.
    Set include_expose=False to only include published host `ports:`.
    """
    if not compose_path.exists() or not compose_path.is_file():
        return {}

    key_pattern = re.compile(
        r'^(\s*)(?:'
        r'"([^"]+)"|'
        r"'([^']+)'|"
        r"([A-Za-z0-9_.-]+)"
        r')\s*:\s*(?:$|#)'
    )
    item_pattern = re.compile(r'^\s*-\s*("?)([^"]+)\1\s*(?:#.*)?$')
    ports = {}
    services_indent: Optional[int] = None
    service_indent: Optional[int] = None
    current_service: Optional[str] = None
    current_service_indent: Optional[int] = None
    section: Optional[str] = None
    section_indent: Optional[int] = None

    lines = compose_path.read_text(encoding="utf-8").splitlines()
    for raw_line in lines:
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
        if key_match is not None:
            key_indent = len(key_match.group(1))
            name = key_match.group(2) or key_match.group(3) or key_match.group(4) or ""
            if service_indent is None and name:
                service_indent = key_indent
            if name and service_indent is not None and key_indent == service_indent:
                current_service = name
                current_service_indent = key_indent
                section = None
                section_indent = None
                continue

        if current_service is None or current_service_indent is None:
            continue
        if indent <= current_service_indent:
            current_service = None
            section = None
            section_indent = None
            continue

        section_match = re.match(r"^\s*(ports|expose)\s*:\s*(?:$|#)", raw_line)
        if section_match is not None:
            section = section_match.group(1)
            section_indent = indent
            continue

        if section is not None and section_indent is not None and indent <= section_indent:
            section = None
            section_indent = None

        if (
            section in ("ports", "expose")
            and section_indent is not None
            and indent > section_indent
        ):
            if section == "expose" and not include_expose:
                continue
            item_match = item_pattern.match(raw_line)
            if item_match is None:
                continue
            if current_service in ports:
                continue
            parsed_port = _extract_container_port(item_match.group(2))
            if parsed_port is not None:
                ports[current_service] = parsed_port

    return ports


def list_compose_service_host_ports(compose_path: Path) -> dict:
    """
    Best-effort parser for first published host port per compose service.
    Reads only `ports:` entries and ignores `expose:`.
    """
    if not compose_path.exists() or not compose_path.is_file():
        return {}

    key_pattern = re.compile(
        r'^(\s*)(?:'
        r'"([^"]+)"|'
        r"'([^']+)'|"
        r"([A-Za-z0-9_.-]+)"
        r')\s*:\s*(?:$|#)'
    )
    item_pattern = re.compile(r'^\s*-\s*("?)([^"]+)\1\s*(?:#.*)?$')
    ports = {}
    services_indent: Optional[int] = None
    service_indent: Optional[int] = None
    current_service: Optional[str] = None
    current_service_indent: Optional[int] = None
    section: Optional[str] = None
    section_indent: Optional[int] = None

    lines = compose_path.read_text(encoding="utf-8").splitlines()
    for raw_line in lines:
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
        if key_match is not None:
            key_indent = len(key_match.group(1))
            name = key_match.group(2) or key_match.group(3) or key_match.group(4) or ""
            if service_indent is None and name:
                service_indent = key_indent
            if name and service_indent is not None and key_indent == service_indent:
                current_service = name
                current_service_indent = key_indent
                section = None
                section_indent = None
                continue

        if current_service is None or current_service_indent is None:
            continue
        if indent <= current_service_indent:
            current_service = None
            section = None
            section_indent = None
            continue

        section_match = re.match(r"^\s*ports\s*:\s*(?:$|#)", raw_line)
        if section_match is not None:
            section = "ports"
            section_indent = indent
            continue

        if section is not None and section_indent is not None and indent <= section_indent:
            section = None
            section_indent = None

        if section == "ports" and section_indent is not None and indent > section_indent:
            item_match = item_pattern.match(raw_line)
            if item_match is None:
                continue
            if current_service in ports:
                continue
            parsed_port = _extract_host_port(item_match.group(2))
            if parsed_port is not None:
                ports[current_service] = parsed_port

    return ports


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
    ingress_mode: IngressMode = IngressMode.MANAGED
    registry_retries: int = 4
    retry_backoff_seconds: int = 5
    tune_docker_daemon: bool = True
    compose_services: Optional[Tuple[str, ...]] = None
    domain: Optional[str] = None
    certbot_email: Optional[str] = None
    auth_token: Optional[str] = None
    proxy_http_port: Optional[int] = None
    proxy_https_port: Optional[int] = None
    proxy_routes: Optional[Tuple[ProxyRoute, ...]] = None
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
        proxy_routes_raw = self.proxy_routes
        proxy_upstream_service = (
            str(self.proxy_upstream_service).strip()
            if self.proxy_upstream_service is not None
            else None
        )
        normalized_routes: List[ProxyRoute] = []
        if proxy_routes_raw is not None:
            seen_keys = set()
            for item in proxy_routes_raw:
                route = item if isinstance(item, ProxyRoute) else parse_proxy_route(item)
                key = (route.host, route.path_prefix)
                if key in seen_keys:
                    raise ValueError(
                        "proxy_routes contains duplicate host/path: "
                        f"{route.host}{route.path_prefix}"
                    )
                seen_keys.add(key)
                normalized_routes.append(route)
            object.__setattr__(self, "proxy_routes", tuple(normalized_routes))

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
        if self.proxy_http_port is not None and not (
            1 <= int(self.proxy_http_port) <= 65535
        ):
            raise ValueError("proxy_http_port must be between 1 and 65535.")
        if self.proxy_https_port is not None and not (
            1 <= int(self.proxy_https_port) <= 65535
        ):
            raise ValueError("proxy_https_port must be between 1 and 65535.")

        if self.auth_token is not None and not _TOKEN_RE.fullmatch(self.auth_token):
            raise ValueError(
                "auth_token must be >= 8 chars and only contain [A-Za-z0-9._~+-]."
            )
        if self.proxy_routes and (
            self.proxy_upstream_service is not None or self.proxy_upstream_port is not None
        ):
            raise ValueError(
                "proxy_routes cannot be combined with proxy_upstream_service/proxy_upstream_port."
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
            if self.ingress_mode != IngressMode.MANAGED and self.access_mode != AccessMode.PUBLIC:
                raise ValueError(
                    "ingress_mode external-nginx/takeover requires access_mode=public."
                )
            if not self.tls_enabled and self.proxy_https_port is not None:
                raise ValueError("proxy_https_port requires domain/certbot mode.")
            if self.ingress_mode != IngressMode.MANAGED and (
                self.proxy_http_port is not None or self.proxy_https_port is not None
            ):
                raise ValueError(
                    "proxy_http_port/proxy_https_port are only used with ingress_mode=managed."
                )
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
            if self.proxy_routes:
                known_services = set(
                    list_compose_services(self.source_compose_path or Path("/__none__"))
                )
                for route in self.proxy_routes:
                    if self.tls_enabled and not _DOMAIN_RE.fullmatch(route.host):
                        raise ValueError(
                            f"proxy_route host '{route.host}' must be a valid DNS "
                            "name for certbot HTTP-01."
                        )
                    if (
                        resolved_kind == SourceKind.COMPOSE
                        and known_services
                        and route.upstream_host in known_services
                        and self.compose_services
                        and route.upstream_host not in self.compose_services
                    ):
                        raise ValueError(
                            f"proxy_route upstream '{route.upstream_host}' must be "
                            "included in compose_services."
                        )
                    if (
                        resolved_kind == SourceKind.COMPOSE
                        and self.ingress_mode != IngressMode.MANAGED
                        and known_services
                        and route.upstream_host in known_services
                    ):
                        raise ValueError(
                            "external-nginx/takeover cannot use compose service names as "
                            f"upstreams ('{route.upstream_host}'). Use a host-reachable "
                            "upstream like 127.0.0.1:<published-port>."
                        )
            _ = self.effective_proxy_upstream_service
            _ = self.effective_proxy_upstream_port
            if self.ingress_mode == IngressMode.MANAGED:
                _ = self.effective_proxy_http_port
                if self.tls_enabled:
                    _ = self.effective_proxy_https_port
                    if self.effective_proxy_http_port == self.effective_proxy_https_port:
                        raise ValueError(
                            "proxy_http_port and proxy_https_port must be different."
                        )
            if (
                self.ingress_mode != IngressMode.MANAGED
                and self.source_kind == SourceKind.COMPOSE
                and not self.proxy_routes
            ):
                raise ValueError(
                    "Compose + external-nginx/takeover requires explicit proxy_routes "
                    "(HOST=UPSTREAM:PORT) that host nginx can reach."
                )
        else:
            if (
                self.auth_token is not None
                or self.proxy_http_port is not None
                or self.proxy_https_port is not None
                or self.ingress_mode != IngressMode.MANAGED
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
    def host_nginx_site_name(self) -> str:
        return f"deploy_wizard_{self.compose_project_name}.conf"

    @property
    def host_nginx_site_available_path(self) -> Path:
        return Path("/etc/nginx/sites-available") / self.host_nginx_site_name

    @property
    def host_nginx_site_enabled_path(self) -> Path:
        return Path("/etc/nginx/sites-enabled") / self.host_nginx_site_name

    @property
    def host_certbot_webroot_path(self) -> Path:
        return self.service_dir / "certbot-www-host"

    @property
    def uses_managed_ingress(self) -> bool:
        return self.reverse_proxy_enabled and self.ingress_mode == IngressMode.MANAGED

    @property
    def tls_enabled(self) -> bool:
        return self.domain is not None

    @property
    def reverse_proxy_enabled(self) -> bool:
        return self.tls_enabled or self.auth_token is not None or self.proxy_routes is not None

    @property
    def effective_bind_host(self) -> str:
        if self.access_mode == AccessMode.PUBLIC:
            return "0.0.0.0"
        return self.bind_host

    @property
    def effective_proxy_routes(self) -> Tuple[ProxyRoute, ...]:
        if not self.reverse_proxy_enabled:
            raise ValueError("No routes without proxy mode.")
        if self.proxy_routes:
            return self.proxy_routes
        if self.ingress_mode != IngressMode.MANAGED:
            if self.source_kind == SourceKind.DOCKERFILE and self.host_port is not None:
                host = self.domain if self.domain is not None else "_"
                return (
                    ProxyRoute(
                        host=host,
                        upstream_host="127.0.0.1",
                        upstream_port=self.host_port,
                    ),
                )
            raise ValueError(
                "external-nginx/takeover requires explicit proxy_routes, "
                "or dockerfile mode with host_port set."
            )
        host = self.domain if self.domain is not None else "_"
        return (
            ProxyRoute(
                host=host,
                upstream_host=self.effective_proxy_upstream_service,
                upstream_port=self.effective_proxy_upstream_port,
            ),
        )

    @property
    def cert_domain_names(self) -> Tuple[str, ...]:
        if not self.tls_enabled:
            return tuple()
        names: List[str] = []
        if self.domain is not None:
            names.append(self.domain)
        for route in self.effective_proxy_routes:
            if _DOMAIN_RE.fullmatch(route.host) and route.host not in names:
                names.append(route.host)
        return tuple(names)

    @property
    def effective_proxy_upstream_service(self) -> str:
        if not self.reverse_proxy_enabled:
            raise ValueError("No upstream service without proxy mode.")
        if self.proxy_routes:
            return self.proxy_routes[0].upstream_host
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
        if self.proxy_routes:
            return self.proxy_routes[0].upstream_port
        if self.proxy_upstream_port is not None:
            return int(self.proxy_upstream_port)
        if self.container_port is not None:
            return int(self.container_port)
        raise ValueError(
            "Proxy mode requires proxy_upstream_port "
            "(or container_port for dockerfile sources)."
        )

    @property
    def effective_proxy_http_port(self) -> int:
        if not self.reverse_proxy_enabled:
            raise ValueError("No HTTP proxy port without proxy mode.")
        if self.proxy_http_port is not None:
            return int(self.proxy_http_port)
        return 80

    @property
    def effective_proxy_https_port(self) -> int:
        if not self.tls_enabled:
            raise ValueError("No HTTPS proxy port without TLS mode.")
        if self.proxy_https_port is not None:
            return int(self.proxy_https_port)
        return 443
