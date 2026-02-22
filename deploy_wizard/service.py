"""
Service deployment logic for compose-backed and Dockerfile-backed sources.
"""

from __future__ import annotations

import subprocess
import socket
import time
from pathlib import Path
from shlex import quote
from typing import Tuple

from deploy_wizard.config import AccessMode, Config, SourceKind
from deploy_wizard.log import die, log_line, sh


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _is_loopback_host(value: str) -> bool:
    host = value.strip().lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _resolve_tailscale_ipv4() -> str:
    proc = subprocess.run(
        ["tailscale", "ip", "-4"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        die(
            "access_mode=tailscale requires a running tailscale client. "
            "Install and connect Tailscale, or pass --bind-host with your Tailscale IP."
        )
    for line in proc.stdout.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    die("Could not detect a Tailscale IPv4 address from `tailscale ip -4`.")


def _resolve_bind_host(cfg: Config) -> str:
    if cfg.access_mode == AccessMode.PUBLIC:
        return "0.0.0.0"
    if cfg.access_mode == AccessMode.TAILSCALE:
        if cfg.bind_host and not _is_loopback_host(cfg.bind_host):
            return cfg.bind_host
        return _resolve_tailscale_ipv4()
    return cfg.bind_host


def _can_bind(bind_host: str, port: int) -> Tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def _suggest_port(bind_host: str, start: int) -> int:
    for candidate in range(max(1024, start), min(start + 2000, 65535) + 1):
        ok, _ = _can_bind(bind_host, candidate)
        if ok:
            return candidate
    return 0


def ensure_required_ports_available(cfg: Config) -> None:
    checks = []
    if cfg.reverse_proxy_enabled:
        bind_host = _resolve_bind_host(cfg)
        checks.append(("proxy HTTP", bind_host, cfg.effective_proxy_http_port, "--proxy-http-port"))
        if cfg.tls_enabled:
            checks.append(("proxy HTTPS", bind_host, cfg.effective_proxy_https_port, "--proxy-https-port"))
    elif cfg.source_kind == SourceKind.DOCKERFILE and cfg.host_port is not None:
        checks.append(("service host port", _resolve_bind_host(cfg), cfg.host_port, "--host-port"))

    for label, bind_host, port, flag in checks:
        ok, err = _can_bind(bind_host, int(port))
        if ok:
            continue
        suggestion = _suggest_port(bind_host, 8080 if int(port) < 1024 else int(port) + 1)
        hint = f" Try {flag} {suggestion}." if suggestion else ""
        die(
            f"{label} {bind_host}:{port} is unavailable ({err}).{hint}"
        )


def write_generated_compose(cfg: Config) -> None:
    ports_block = ""
    if cfg.host_port is not None and cfg.container_port is not None:
        bind_host = _resolve_bind_host(cfg)
        ports_block = (
            "    ports:\n"
            f'      - "{bind_host}:{cfg.host_port}:{cfg.container_port}"\n'
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


def write_proxy_compose(cfg: Config) -> None:
    if not cfg.reverse_proxy_enabled:
        return
    nginx_conf = cfg.managed_nginx_conf_path
    bind_host = _resolve_bind_host(cfg)
    acme_dir = cfg.service_dir / "certbot-www"
    letsencrypt_dir = cfg.service_dir / "letsencrypt"
    if cfg.tls_enabled:
        acme_dir.mkdir(parents=True, exist_ok=True)
        letsencrypt_dir.mkdir(parents=True, exist_ok=True)
    ports = [f'      - "{bind_host}:{cfg.effective_proxy_http_port}:80"\n']
    if cfg.tls_enabled:
        ports.append(f'      - "{bind_host}:{cfg.effective_proxy_https_port}:443"\n')
    volumes = [f'      - "{nginx_conf}:/etc/nginx/conf.d/default.conf:ro"\n']
    if cfg.tls_enabled:
        volumes.extend(
            [
                f'      - "{acme_dir}:/var/www/certbot"\n',
                f'      - "{letsencrypt_dir}:/etc/letsencrypt"\n',
            ]
        )
    content = (
        "services:\n"
        "  nginx:\n"
        "    image: nginx:1.27-alpine\n"
        f"    container_name: {cfg.compose_project_name}-nginx\n"
        "    restart: unless-stopped\n"
        "    ports:\n"
        f"{''.join(ports)}"
        "    volumes:\n"
        f"{''.join(volumes)}"
    )
    if cfg.tls_enabled:
        content += (
            "  certbot:\n"
            "    image: certbot/certbot:latest\n"
            '    profiles: ["manual"]\n'
            "    volumes:\n"
            f'      - "{acme_dir}:/var/www/certbot"\n'
            f'      - "{letsencrypt_dir}:/etc/letsencrypt"\n'
        )
    write_file(cfg.managed_proxy_compose_path, content)


def write_nginx_proxy_config(cfg: Config, *, https_enabled: bool) -> None:
    if not cfg.reverse_proxy_enabled:
        return
    routes = cfg.effective_proxy_routes
    cert_base_domain = cfg.domain or ""
    auth_guard = ""
    if cfg.auth_token is not None:
        auth_guard = (
            f'        if ($http_authorization != "Bearer {cfg.auth_token}") {{\n'
            "            return 401;\n"
            "        }\n"
        )
    route_blocks = []
    if not cfg.tls_enabled:
        for route in routes:
            route_blocks.append(
                (
                    "server {\n"
                    "    listen 80;\n"
                    f"    server_name {route.host};\n"
                    "\n"
                    "    location / {\n"
                    f"{auth_guard}"
                    f"        proxy_pass http://{route.upstream_host}:{route.upstream_port};\n"
                    "        proxy_http_version 1.1;\n"
                    "        proxy_set_header Host $host;\n"
                    "        proxy_set_header X-Real-IP $remote_addr;\n"
                    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    "    }\n"
                    "}\n"
                )
            )
    elif not https_enabled:
        for route in routes:
            route_blocks.append(
                (
                    "server {\n"
                    "    listen 80;\n"
                    f"    server_name {route.host};\n"
                    "\n"
                    "    location /.well-known/acme-challenge/ {\n"
                    "        root /var/www/certbot;\n"
                    "    }\n"
                    "\n"
                    "    location / {\n"
                    f"{auth_guard}"
                    f"        proxy_pass http://{route.upstream_host}:{route.upstream_port};\n"
                    "        proxy_http_version 1.1;\n"
                    "        proxy_set_header Host $host;\n"
                    "        proxy_set_header X-Real-IP $remote_addr;\n"
                    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    "    }\n"
                    "}\n"
                )
            )
    else:
        for route in routes:
            route_blocks.append(
                (
                    "server {\n"
                    "    listen 80;\n"
                    f"    server_name {route.host};\n"
                    "\n"
                    "    location /.well-known/acme-challenge/ {\n"
                    "        root /var/www/certbot;\n"
                    "    }\n"
                    "\n"
                    "    location / {\n"
                    "        return 301 https://$host$request_uri;\n"
                    "    }\n"
                    "}\n"
                )
            )
            route_blocks.append(
                (
                    "server {\n"
                    "    listen 443 ssl;\n"
                    f"    server_name {route.host};\n"
                    "\n"
                    f"    ssl_certificate /etc/letsencrypt/live/{cert_base_domain}/fullchain.pem;\n"
                    f"    ssl_certificate_key /etc/letsencrypt/live/{cert_base_domain}/privkey.pem;\n"
                    "    ssl_protocols TLSv1.2 TLSv1.3;\n"
                    "    ssl_prefer_server_ciphers on;\n"
                    "\n"
                    "    location / {\n"
                    f"{auth_guard}"
                    f"        proxy_pass http://{route.upstream_host}:{route.upstream_port};\n"
                    "        proxy_http_version 1.1;\n"
                    "        proxy_set_header Host $host;\n"
                    "        proxy_set_header X-Real-IP $remote_addr;\n"
                    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                    "    }\n"
                    "}\n"
                )
            )
    content = "\n".join(route_blocks) + "\n"
    write_file(cfg.managed_nginx_conf_path, content)


def _compose_workdir(cfg: Config) -> Path:
    if cfg.source_kind == SourceKind.COMPOSE:
        return cfg.source_dir
    return cfg.service_dir


def _compose_prefix(cfg: Config) -> str:
    if cfg.source_kind == SourceKind.COMPOSE:
        base_compose = cfg.source_compose_path
    else:
        base_compose = cfg.managed_compose_path
    if base_compose is None:
        raise ValueError("Missing base compose file.")
    files = [base_compose]
    if cfg.reverse_proxy_enabled:
        files.append(cfg.managed_proxy_compose_path)
    files_part = " ".join(f"-f {quote(str(path))}" for path in files)
    return f"docker compose -p {quote(cfg.compose_project_name)} {files_part}"


def _issue_certificate(cfg: Config) -> None:
    if not cfg.tls_enabled:
        return
    domains = cfg.cert_domain_names
    if not domains:
        die("No certificate domains configured for certbot.")
    primary_domain = domains[0]
    certbot_email = cfg.certbot_email or ""
    domains_arg = " ".join(f"-d {quote(name)}" for name in domains)
    cmd = (
        f"cd {quote(str(_compose_workdir(cfg)))} && "
        f"{_compose_prefix(cfg)} run --rm certbot "
        f"certonly --webroot -w /var/www/certbot "
        f"--agree-tos --non-interactive --no-eff-email "
        f"--email {quote(certbot_email)} "
        f"{domains_arg} "
        "--keep-until-expiring"
    )
    if not _run_with_retries(
        cmd,
        attempts=cfg.registry_retries,
        backoff_seconds=cfg.retry_backoff_seconds,
        context="certbot certificate issuance",
    ):
        die(
            "Certbot certificate issuance failed after retries. "
            f"Check DNS A/AAAA records and firewall rules for port 80. "
            f"Primary cert domain: {primary_domain}."
        )


def _reload_nginx(cfg: Config) -> None:
    if not cfg.reverse_proxy_enabled:
        return
    workdir = quote(str(_compose_workdir(cfg)))
    prefix = _compose_prefix(cfg)
    reload_cmd = f"cd {workdir} && {prefix} exec -T nginx nginx -s reload"
    if sh(reload_cmd, check=False) == 0:
        return
    up_cmd = f"cd {workdir} && {prefix} up -d nginx"
    if sh(up_cmd, check=False) != 0:
        die("Failed to start nginx container for TLS reload.")
    if sh(reload_cmd, check=False) != 0:
        die("Failed to reload nginx after updating TLS configuration.")


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
    services = []
    if cfg.compose_services:
        services.extend(cfg.compose_services)
    if cfg.reverse_proxy_enabled:
        write_proxy_compose(cfg)
        write_nginx_proxy_config(cfg, https_enabled=False)
        if cfg.compose_services and "nginx" not in services:
            services.append("nginx")
    services_arg = ""
    if services:
        services_arg = " " + " ".join(quote(s) for s in services)
    cmd = (
        f"cd {quote(str(_compose_workdir(cfg)))} && "
        f"{_compose_prefix(cfg)} up -d --build{services_arg}"
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
    if cfg.tls_enabled:
        _issue_certificate(cfg)
        write_nginx_proxy_config(cfg, https_enabled=True)
        _reload_nginx(cfg)


def deploy_dockerfile_source(cfg: Config) -> None:
    write_generated_compose(cfg)
    services = cfg.service_key
    if cfg.reverse_proxy_enabled:
        write_proxy_compose(cfg)
        write_nginx_proxy_config(cfg, https_enabled=False)
        services = f"{cfg.service_key} nginx"
    cmd = (
        f"cd {quote(str(_compose_workdir(cfg)))} && "
        f"{_compose_prefix(cfg)} up -d --build {services}"
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
    if cfg.tls_enabled:
        _issue_certificate(cfg)
        write_nginx_proxy_config(cfg, https_enabled=True)
        _reload_nginx(cfg)


def deploy_service(cfg: Config) -> None:
    if cfg.source_kind == SourceKind.COMPOSE:
        deploy_compose_source(cfg)
        return
    if cfg.source_kind == SourceKind.DOCKERFILE:
        deploy_dockerfile_source(cfg)
        return
    raise ValueError(f"Unsupported source kind: {cfg.source_kind}")
