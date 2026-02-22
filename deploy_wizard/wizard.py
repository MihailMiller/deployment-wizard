"""
Interactive wizard for generic service deployment.
"""

from __future__ import annotations

import re
import socket
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from deploy_wizard.config import (
    AccessMode,
    Config,
    IngressMode,
    parse_proxy_route,
    SourceKind,
    detect_source_kind,
    find_compose_file,
    list_missing_compose_env_vars,
    list_compose_service_host_ports,
    list_compose_service_ports,
    list_compose_services,
)


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value if value else default


def _prompt_int(msg: str, default: int, min_val: int = 1, max_val: int = 65535) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            value = int(raw)
            if min_val <= value <= max_val:
                return value
        except ValueError:
            pass
        print(f"Please enter a number between {min_val} and {max_val}.")


def _confirm(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = _prompt(f"{msg} ({hint})", "").lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _choose(options: List[Tuple[str, str]], default: int = 1) -> int:
    for idx, (name, desc) in enumerate(options, 1):
        print(f"  [{idx}] {name} - {desc}")
    while True:
        raw = _prompt("Choice", str(default))
        try:
            value = int(raw)
            if 1 <= value <= len(options):
                return value
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(options)}.")


def _choose_access_mode() -> AccessMode:
    options = [
        (AccessMode.LOCALHOST.value, "Bind to loopback only"),
        (AccessMode.TAILSCALE.value, "Bind to Tailscale interface IP"),
        (AccessMode.PUBLIC.value, "Bind to all interfaces (0.0.0.0)"),
    ]
    idx = _choose(options, default=1)
    return AccessMode(options[idx - 1][0])


def _choose_ingress_mode() -> IngressMode:
    options = [
        (IngressMode.MANAGED.value, "Managed docker nginx + certbot"),
        (IngressMode.EXTERNAL_NGINX.value, "Use existing host nginx + certbot"),
        (IngressMode.TAKEOVER.value, "Stop/start host nginx during reconfigure"),
    ]
    idx = _choose(options, default=1)
    return IngressMode(options[idx - 1][0])


def _default_service_key(service_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]", "-", service_name.lower())
    normalized = normalized.strip("-_")
    return normalized or "service"


def _default_route_path_segment(name: str) -> str:
    token = re.sub(r"[^a-z0-9._~-]+", "-", name.lower()).strip("-")
    return token or "service"


def _default_subdomain_label(name: str) -> str:
    token = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    token = re.sub(r"-+", "-", token)
    if not token:
        return "service"
    return token[:63].strip("-") or "service"


def _format_route_spec(route) -> str:
    if route.path_prefix == "/":
        return f"{route.host}={route.upstream_host}:{route.upstream_port}"
    return f"{route.host}{route.path_prefix}={route.upstream_host}:{route.upstream_port}"


def _format_route_summary(route) -> str:
    return f"{route.host}{route.path_prefix}->{route.upstream_host}:{route.upstream_port}"


def _route_url_hint(route, *, tls_enabled: bool, default_domain: Optional[str]) -> str:
    host = route.host
    if host == "_" and default_domain is not None:
        host = default_domain
    if host == "_":
        return route.path_prefix
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{host}{route.path_prefix}"


def _build_compose_path_routes(
    *,
    host: str,
    services: List[str],
    service_ports: dict,
) -> Tuple[str, ...]:
    routes: List[str] = []
    used_paths = set()
    for service in services:
        upstream_port_raw = service_ports.get(service)
        if upstream_port_raw is None:
            continue
        base = f"/{_default_route_path_segment(service)}"
        path = base
        suffix = 2
        while path in used_paths:
            path = f"{base}-{suffix}"
            suffix += 1
        used_paths.add(path)
        upstream_port = int(upstream_port_raw)
        route = parse_proxy_route(f"{host}{path}={service}:{upstream_port}")
        routes.append(_format_route_spec(route))
    return tuple(routes)


def _build_compose_subdomain_routes(
    *,
    domain: str,
    services: List[str],
    service_ports: dict,
) -> Tuple[str, ...]:
    routes: List[str] = []
    used_hosts = set()
    for service in services:
        upstream_port_raw = service_ports.get(service)
        if upstream_port_raw is None:
            continue
        base = f"{_default_subdomain_label(service)}.{domain}".lower()
        host = base
        suffix = 2
        while host in used_hosts:
            host = f"{_default_subdomain_label(service)}-{suffix}.{domain}".lower()
            suffix += 1
        used_hosts.add(host)
        upstream_port = int(upstream_port_raw)
        route = parse_proxy_route(f"{host}={service}:{upstream_port}")
        routes.append(_format_route_spec(route))
    return tuple(routes)


def _build_compose_subdomain_host_routes(
    *,
    domain: str,
    services: List[str],
    host_ports: dict,
) -> Tuple[str, ...]:
    routes: List[str] = []
    used_hosts = set()
    for service in services:
        upstream_port_raw = host_ports.get(service)
        if upstream_port_raw is None:
            continue
        base = f"{_default_subdomain_label(service)}.{domain}".lower()
        host = base
        suffix = 2
        while host in used_hosts:
            host = f"{_default_subdomain_label(service)}-{suffix}.{domain}".lower()
            suffix += 1
        used_hosts.add(host)
        route = parse_proxy_route(f"{host}=127.0.0.1:{int(upstream_port_raw)}")
        routes.append(_format_route_spec(route))
    return tuple(routes)


def _port_available(bind_host: str, port: int) -> Tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def _suggest_port(bind_host: str, start: int, avoid: Optional[set] = None) -> Optional[int]:
    blocked = avoid or set()
    presets = [8080, 8081, 8088, 8888, 9000, 9443]
    for candidate in presets:
        if candidate in blocked:
            continue
        if candidate < start:
            continue
        ok, _ = _port_available(bind_host, candidate)
        if ok:
            return candidate
    upper = min(start + 500, 65535)
    for candidate in range(max(1024, start), upper + 1):
        if candidate in blocked:
            continue
        ok, _ = _port_available(bind_host, candidate)
        if ok:
            return candidate
    return None


def _pick_open_port(
    label: str,
    bind_host: str,
    default: int,
    avoid: Optional[set] = None,
) -> int:
    blocked = avoid or set()
    while True:
        port = _prompt_int(label, default)
        if port in blocked:
            print(f"Port {port} is already reserved in this deployment. Choose another port.")
            continue
        ok, err = _port_available(bind_host, port)
        if ok:
            return port
        print(f"Port {bind_host}:{port} is unavailable: {err}")
        suggestion = _suggest_port(
            bind_host,
            8080 if port < 1024 else port + 1,
            avoid=blocked,
        )
        if suggestion is not None and _confirm(f"Use suggested port {suggestion}?", default=True):
            return suggestion
        print("Choose another port.")


def _auto_pick_port(
    *,
    bind_host: str,
    preferred: int,
    avoid: Optional[set] = None,
    label: str,
) -> int:
    blocked = avoid or set()
    if preferred not in blocked:
        ok, _ = _port_available(bind_host, preferred)
        if ok:
            print(f"{label}: using {bind_host}:{preferred}")
            return preferred
        _, err = _port_available(bind_host, preferred)
        print(f"{label}: {bind_host}:{preferred} unavailable ({err}).")

    suggestion = _suggest_port(
        bind_host,
        8080 if preferred < 1024 else preferred + 1,
        avoid=blocked,
    )
    if suggestion is None:
        print(f"{label}: no free port found automatically, falling back to manual entry.")
        return _pick_open_port(label, bind_host, preferred, avoid=blocked)
    print(f"{label}: auto-selected {bind_host}:{suggestion}")
    return suggestion


def _pick_proxy_routes(
    *,
    default_host: str,
    default_upstream: str,
    default_port: int,
    default_path_prefix: str = "/",
) -> Optional[Tuple[str, ...]]:
    if not _confirm("Configure hostname-based proxy routes?", default=False):
        return None
    print(
        "Enter routes as <host>[/path]=<upstream>:<port>. "
        "Example: wiki.example.com/orchestrator=orchestrator:8090"
    )
    routes: List[str] = []
    default_route = (
        f"{default_host}={default_upstream}:{default_port}"
        if default_path_prefix == "/"
        else f"{default_host}{default_path_prefix}={default_upstream}:{default_port}"
    )
    while True:
        prompt_default = default_route if not routes else ""
        raw = _prompt("Proxy route", prompt_default)
        try:
            route = parse_proxy_route(raw)
        except ValueError as exc:
            print(f"Invalid route: {exc}")
            continue
        routes.append(_format_route_spec(route))
        if not _confirm("Add another route?", default=False):
            break
    return tuple(routes)


def _choose_services(services: List[str]) -> Optional[Tuple[str, ...]]:
    print("Compose services found:")
    for idx, name in enumerate(services, 1):
        print(f"  [{idx}] {name}")
    print("Enter comma-separated numbers or names, or press Enter to deploy all services.")

    while True:
        raw = _prompt("Services", "")
        if not raw:
            return None

        chosen: List[str] = []
        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        for token in tokens:
            if token.isdigit():
                idx = int(token)
                if not (1 <= idx <= len(services)):
                    chosen = []
                    break
                name = services[idx - 1]
            else:
                if token not in services:
                    chosen = []
                    break
                name = token
            if name not in chosen:
                chosen.append(name)

        if chosen:
            return tuple(chosen)
        print("Invalid selection. Use listed numbers or exact service names.")


def _pick_source_dir() -> Tuple[Path, SourceKind]:
    while True:
        raw = _prompt("Source directory", str(Path.cwd()))
        source_dir = Path(raw).expanduser()
        if not source_dir.exists() or not source_dir.is_dir():
            print("Directory does not exist.")
            continue

        compose_path = find_compose_file(source_dir)
        has_dockerfile = (source_dir / "Dockerfile").exists()
        if compose_path and has_dockerfile:
            print("Both docker-compose and Dockerfile found.")
            idx = _choose(
                [
                    ("Use compose file", f"{compose_path.name}"),
                    ("Use Dockerfile", "Generate managed compose under service name"),
                ],
                default=1,
            )
            return source_dir, (SourceKind.COMPOSE if idx == 1 else SourceKind.DOCKERFILE)
        if compose_path:
            return source_dir, SourceKind.COMPOSE
        if has_dockerfile:
            return source_dir, SourceKind.DOCKERFILE

        print("No docker-compose.yml/compose.yml or Dockerfile found in this directory.")
        try:
            detect_source_kind(source_dir)
        except ValueError:
            pass


def _dotenv_quote(value: str) -> str:
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@+\-]+", text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_dotenv_values(dotenv_path: Path, pairs: List[Tuple[str, str]]) -> None:
    updates = {key: value for key, value in pairs}
    lines: List[str] = []
    if dotenv_path.exists() and dotenv_path.is_file():
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    rendered: List[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rendered.append(line)
            continue

        candidate = stripped
        prefix = ""
        if candidate.startswith("export "):
            prefix = "export "
            candidate = candidate[len("export ") :].strip()
        key_raw, _value_raw = candidate.split("=", 1)
        key = key_raw.strip()
        if key in remaining:
            rendered.append(f"{prefix}{key}={_dotenv_quote(remaining.pop(key))}")
        else:
            rendered.append(line)

    if remaining:
        if rendered and rendered[-1].strip():
            rendered.append("")
        for key, value in pairs:
            if key in remaining:
                rendered.append(f"{key}={_dotenv_quote(value)}")
                remaining.pop(key)

    dotenv_path.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _collect_missing_compose_env(compose_path: Path) -> None:
    missing = list_missing_compose_env_vars(
        compose_path,
        dotenv_path=compose_path.parent / ".env",
        env={},
    )
    if not missing:
        return

    print("Compose file uses variables that are currently unset or empty:")
    print("  " + ", ".join(name for name, _requires_non_empty in missing))
    print("Provide values now; they will be written to .env in the source directory.")

    updates: List[Tuple[str, str]] = []
    for name, _requires_non_empty in missing:
        while True:
            value = _prompt(f"Value for {name}", "")
            if value == "":
                print("Value must not be empty.")
                continue
            updates.append((name, value))
            break

    dotenv_path = compose_path.parent / ".env"
    _upsert_dotenv_values(dotenv_path, updates)
    print(f"Wrote {len(updates)} variable(s) to {dotenv_path}.")


def run_wizard() -> Config:
    print()
    print("Generic Service Deployment Wizard")
    print("Deploy any Docker microservice from a local directory.")
    print()

    service_name = _prompt("Service name", "my-service")
    source_dir, source_kind = _pick_source_dir()
    base_dir = Path(_prompt("Deployment base directory", "/opt/services")).expanduser()

    host_port: Optional[int] = None
    container_port: Optional[int] = None
    bind_host = "127.0.0.1"
    access_mode = AccessMode.LOCALHOST
    ingress_mode = IngressMode.MANAGED
    compose_services: Optional[Tuple[str, ...]] = None
    discovered_services: List[str] = []
    discovered_service_ports = {}
    discovered_published_ports = {}
    discovered_host_ports = {}
    domain: Optional[str] = None
    certbot_email: Optional[str] = None
    auth_token: Optional[str] = None
    proxy_upstream_service: Optional[str] = None
    proxy_upstream_port: Optional[int] = None
    proxy_routes: Optional[Tuple[str, ...]] = None
    proxy_http_port: Optional[int] = None
    proxy_https_port: Optional[int] = None

    print("Access mode:")
    access_mode = _choose_access_mode()

    if source_kind == SourceKind.COMPOSE:
        compose_path = find_compose_file(source_dir)
        if compose_path is not None:
            discovered_services = list_compose_services(compose_path)
            discovered_service_ports = list_compose_service_ports(compose_path)
            discovered_published_ports = list_compose_service_ports(
                compose_path,
                include_expose=False,
            )
            discovered_host_ports = list_compose_service_host_ports(compose_path)
            if discovered_services:
                compose_services = _choose_services(discovered_services)
            _collect_missing_compose_env(compose_path)

    if source_kind == SourceKind.DOCKERFILE:
        if _confirm("Expose a host port for this service?", default=False):
            container_port = _prompt_int("Container port", 8080)
            host_port = _prompt_int("Host port", container_port)
            if access_mode == AccessMode.PUBLIC:
                bind_host = _prompt("Bind host", "0.0.0.0")
            elif access_mode == AccessMode.TAILSCALE:
                bind_host = _prompt("Bind host (Tailscale IP, optional)", "127.0.0.1")
            else:
                bind_host = _prompt("Bind host", "127.0.0.1")

    if _confirm("Enable nginx reverse proxy with Let's Encrypt?", default=False):
        domain = _prompt("Public domain", "api.example.com").lower()
        certbot_email = _prompt("Let's Encrypt email", "ops@example.com").lower()
        access_mode = AccessMode.PUBLIC

    if _confirm("Require bearer token authentication at proxy?", default=False):
        auth_token = _prompt("Bearer token", "") or None

    if (
        source_kind == SourceKind.COMPOSE
        and access_mode != AccessMode.LOCALHOST
        and domain is None
        and auth_token is None
    ):
        print("Compose source with non-local access requires managed proxy mode.")
        if _confirm("Enable bearer token authentication now?", default=True):
            auth_token = _prompt("Bearer token", "") or None
        else:
            access_mode = AccessMode.LOCALHOST

    proxy_enabled = domain is not None or auth_token is not None
    if proxy_enabled:
        ingress_mode = _choose_ingress_mode()
        proxy_bind_host = "0.0.0.0" if access_mode == AccessMode.PUBLIC else "127.0.0.1"
        if (
            source_kind == SourceKind.DOCKERFILE
            and ingress_mode != IngressMode.MANAGED
            and host_port is None
        ):
            if container_port is None:
                container_port = _prompt_int("Application container port", 8080)
            bind_host = "127.0.0.1"
            host_port = _auto_pick_port(
                bind_host=bind_host,
                preferred=container_port,
                label="Local upstream host port",
            )
            print(
                "Host nginx mode: auto-mapped dockerfile service to "
                f"{bind_host}:{host_port}->{container_port}"
            )
        if ingress_mode == IngressMode.MANAGED:
            if domain is not None:
                proxy_http_port = 80
                proxy_https_port = 443
                http_ok, http_err = _port_available(proxy_bind_host, 80)
                https_ok, https_err = _port_available(proxy_bind_host, 443)
                if not http_ok or not https_ok:
                    print(
                        "Public HTTPS mode expects host ports 80 and 443 to be free "
                        "for Let's Encrypt HTTP-01 and standard browser access."
                    )
                    if not http_ok:
                        print(f"  - {proxy_bind_host}:80 unavailable ({http_err})")
                    if not https_ok:
                        print(f"  - {proxy_bind_host}:443 unavailable ({https_err})")
                    if not _confirm(
                        "Use advanced non-standard proxy ports instead?",
                        default=False,
                    ):
                        print(
                            "Aborted. Free ports 80/443 (or choose external-nginx/takeover) "
                            "and run again."
                        )
                        sys.exit(1)
                    proxy_http_port = _auto_pick_port(
                        bind_host=proxy_bind_host,
                        preferred=80,
                        label="Proxy HTTP host port",
                    )
                    proxy_https_port = _auto_pick_port(
                        bind_host=proxy_bind_host,
                        preferred=443,
                        avoid={proxy_http_port},
                        label="Proxy HTTPS host port",
                    )
                    print(
                        "Warning: Let's Encrypt HTTP-01 normally requires external port 80. "
                        "Use host/network forwarding from :80/:443 to the selected proxy ports."
                    )
            else:
                proxy_http_port = _auto_pick_port(
                    bind_host=proxy_bind_host,
                    preferred=80,
                    label="Proxy HTTP host port",
                )
        elif domain is not None:
            print("Host nginx ingress mode selected; using standard host ports 80/443.")
        default_upstream = _default_service_key(service_name)
        default_upstream_port = container_port or 8080
        if source_kind == SourceKind.COMPOSE:
            selected_default_service = None
            if compose_services:
                selected_default_service = compose_services[0]
            elif discovered_services:
                selected_default_service = discovered_services[0]
            if selected_default_service is not None:
                default_upstream = selected_default_service
            if ingress_mode == IngressMode.MANAGED:
                default_upstream_port = int(discovered_service_ports.get(default_upstream, 8080))
            else:
                default_upstream = "127.0.0.1"
                if selected_default_service is not None:
                    default_upstream_port = int(
                        discovered_host_ports.get(selected_default_service, 8080)
                    )
                else:
                    default_upstream_port = 8080
        default_host = domain or "_"
        default_path_prefix = "/"
        if source_kind == SourceKind.COMPOSE:
            selected_services = list(compose_services or discovered_services)
            if ingress_mode == IngressMode.MANAGED:
                route_candidates = [
                    svc for svc in selected_services if svc in discovered_service_ports
                ]
            else:
                route_candidates = [
                    svc for svc in selected_services if svc in discovered_host_ports
                ]
            skipped = [svc for svc in selected_services if svc not in discovered_service_ports]
            if ingress_mode != IngressMode.MANAGED:
                skipped = [svc for svc in selected_services if svc not in discovered_host_ports]
            if skipped:
                print(
                    "Skipping route suggestions for services without host-reachable ports: "
                    + ", ".join(skipped)
                )
            if (
                ingress_mode == IngressMode.MANAGED
                and compose_services is None
                and discovered_published_ports
            ):
                published_subset = [
                    svc for svc in route_candidates if svc in discovered_published_ports
                ]
                if published_subset:
                    route_candidates = published_subset
                    print(
                        "Routing suggestions limited to services with published host ports."
                    )
            if len(selected_services) > 1:
                suggested_routes: Tuple[str, ...] = tuple()
                prompt = "Use these suggested /service routes?"
                heading = "Suggested default URLs (one path per selected service):"
                if domain is not None:
                    if ingress_mode == IngressMode.MANAGED:
                        suggested_routes = _build_compose_subdomain_routes(
                            domain=domain,
                            services=route_candidates,
                            service_ports=discovered_service_ports,
                        )
                    else:
                        suggested_routes = _build_compose_subdomain_host_routes(
                            domain=domain,
                            services=route_candidates,
                            host_ports=discovered_host_ports,
                        )
                    prompt = "Use these suggested subdomain routes?"
                    heading = "Suggested default URLs (subdomain per service):"
                else:
                    if ingress_mode == IngressMode.MANAGED:
                        suggested_routes = _build_compose_path_routes(
                            host=default_host,
                            services=route_candidates,
                            service_ports=discovered_service_ports,
                        )
                if suggested_routes:
                    print(heading)
                    for raw_route in suggested_routes:
                        parsed_route = parse_proxy_route(raw_route)
                        url_hint = _route_url_hint(
                            parsed_route,
                            tls_enabled=domain is not None,
                            default_domain=domain,
                        )
                        print(
                            f"  - {url_hint} -> "
                            f"{parsed_route.upstream_host}:{parsed_route.upstream_port}"
                        )
                    if _confirm(prompt, default=True):
                        proxy_routes = suggested_routes
                if ingress_mode == IngressMode.MANAGED:
                    default_path_prefix = f"/{_default_route_path_segment(default_upstream)}"
        if proxy_routes is None:
            proxy_routes = _pick_proxy_routes(
                default_host=default_host,
                default_upstream=default_upstream,
                default_port=default_upstream_port,
                default_path_prefix=default_path_prefix,
            )
        if source_kind == SourceKind.COMPOSE and ingress_mode != IngressMode.MANAGED and proxy_routes is None:
            print("external-nginx/takeover with compose requires at least one hostname route.")
            routes: List[str] = []
            default_route = (
                f"{default_host}={default_upstream}:{default_upstream_port}"
                if default_path_prefix == "/"
                else f"{default_host}{default_path_prefix}={default_upstream}:{default_upstream_port}"
            )
            while True:
                raw = _prompt("Proxy route", default_route if not routes else "")
                try:
                    route = parse_proxy_route(raw)
                except ValueError as exc:
                    print(f"Invalid route: {exc}")
                    continue
                routes.append(_format_route_spec(route))
                if not _confirm("Add another route?", default=False):
                    break
            proxy_routes = tuple(routes)
        if source_kind == SourceKind.COMPOSE:
            if proxy_routes is None:
                proxy_upstream_service = default_upstream
                proxy_upstream_port = default_upstream_port
                print(
                    "Proxy upstream auto-selected: "
                    f"{proxy_upstream_service}:{proxy_upstream_port}"
                )
        else:
            if proxy_routes is None:
                if container_port is not None:
                    proxy_upstream_port = container_port
                else:
                    proxy_upstream_port = _prompt_int("Application container port for proxy", 8080)

    cfg = Config(
        service_name=service_name,
        source_dir=source_dir,
        source_kind=source_kind,
        base_dir=base_dir,
        host_port=host_port,
        container_port=container_port,
        bind_host=bind_host,
        access_mode=access_mode,
        ingress_mode=ingress_mode,
        compose_services=compose_services,
        domain=domain,
        certbot_email=certbot_email,
        auth_token=auth_token,
        proxy_http_port=proxy_http_port,
        proxy_https_port=proxy_https_port,
        proxy_routes=proxy_routes,
        proxy_upstream_service=proxy_upstream_service,
        proxy_upstream_port=proxy_upstream_port,
    )

    print()
    print("Review")
    print(f"  Service name : {cfg.service_name}")
    print(f"  Source dir   : {cfg.source_dir}")
    print(f"  Source kind  : {cfg.source_kind.value}")
    print(f"  Base dir     : {cfg.base_dir}")
    print(f"  Access mode  : {cfg.access_mode.value}")
    print(f"  Ingress mode : {cfg.ingress_mode.value}")
    if cfg.host_port is not None:
        print(
            f"  Port mapping : "
            f"{cfg.effective_bind_host}:{cfg.host_port}->{cfg.container_port}"
        )
    else:
        print("  Port mapping : none")
    if cfg.source_kind == SourceKind.COMPOSE and cfg.compose_services:
        print(f"  Compose svcs : {', '.join(cfg.compose_services)}")
    elif cfg.source_kind == SourceKind.COMPOSE:
        print("  Compose svcs : all")
    if cfg.tls_enabled:
        print(f"  Domain       : {cfg.domain}")
        print(f"  TLS email    : {cfg.certbot_email}")
        print(
            f"  Proxy ports  : "
            f"{cfg.effective_proxy_http_port}->{cfg.effective_proxy_https_port}"
        )
        print(
            f"  Proxy target : "
            f"{cfg.effective_proxy_upstream_service}:{cfg.effective_proxy_upstream_port}"
        )
    elif cfg.reverse_proxy_enabled:
        print(f"  Proxy port   : {cfg.effective_proxy_http_port}")
        print(
            f"  Proxy target : "
            f"{cfg.effective_proxy_upstream_service}:{cfg.effective_proxy_upstream_port}"
        )
    if cfg.proxy_routes:
        rendered = ", ".join(
            _format_route_summary(r)
            for r in cfg.proxy_routes
        )
        print(f"  Proxy routes : {rendered}")
    if cfg.auth_token is not None:
        print("  Auth token   : enabled")
    else:
        print("  Auth token   : disabled")
    print()

    if not _confirm("Proceed with deployment?", default=True):
        print("Aborted.")
        sys.exit(0)

    return cfg
