"""
Deployment orchestrator for generic service deployments.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from shlex import quote
from typing import Any, Callable, List, Optional

from deploy_wizard.log import LOG_PATH, log_line


class _TqdmStub:
    def __init__(self, total: int = 0, desc: str = "", unit: str = "") -> None:
        self.total = total

    @staticmethod
    def write(msg: str) -> None:
        print(msg, flush=True)

    def update(self, _n: int = 1) -> None:
        return

    def __enter__(self) -> "_TqdmStub":
        return self

    def __exit__(self, *_args) -> bool:
        return False


tqdm = _TqdmStub


def _ensure_tqdm() -> None:
    global tqdm
    try:
        from tqdm import tqdm as real_tqdm

        tqdm = real_tqdm
    except Exception:
        tqdm = _TqdmStub


@dataclass
class Step:
    label: str
    fn: Callable[[], Any]
    skip_if: Optional[Callable[[], bool]] = None
    result: Any = field(default=None, repr=False)


def run_steps(steps: List[Step], bar: Any) -> None:
    import time

    for step in steps:
        if step.skip_if and step.skip_if():
            tqdm.write(f"[SKIP] {step.label}")
            log_line(f"[SKIP] {step.label}")
            bar.update(1)
            continue
        tqdm.write(f"\n[STEP] {step.label}")
        log_line(f"[STEP] {step.label}")
        t0 = time.time()
        step.result = step.fn()
        elapsed = time.time() - t0
        tqdm.write(f"[DONE] {step.label} ({elapsed:.1f}s)")
        log_line(f"[DONE] {step.label} ({elapsed:.1f}s)")
        bar.update(1)


def run_deploy(cfg) -> None:
    from deploy_wizard.config import IngressMode
    from deploy_wizard.service import deploy_service, ensure_required_ports_available
    from deploy_wizard.system import (
        detect_ubuntu,
        ensure_base_packages,
        ensure_docker,
        ensure_docker_daemon_tuning,
        ensure_nginx_and_certbot,
        require_root_reexec,
    )

    require_root_reexec()
    detect_ubuntu()
    _ensure_tqdm()

    cfg.service_dir.mkdir(parents=True, exist_ok=True)

    log_line(f"=== START {dt.datetime.now(dt.timezone.utc).isoformat()}Z ===")
    tqdm.write(f"[INFO] Logging to: {LOG_PATH}")
    try:
        steps: List[Step] = [
            Step("Install base packages", ensure_base_packages),
            Step("Install/verify Docker", ensure_docker),
            Step(
                "Harden Docker registry network settings",
                ensure_docker_daemon_tuning,
                skip_if=lambda: not cfg.tune_docker_daemon,
            ),
            Step(
                "Install/verify nginx + certbot",
                ensure_nginx_and_certbot,
                skip_if=lambda: cfg.ingress_mode == IngressMode.MANAGED
                or not cfg.reverse_proxy_enabled,
            ),
            Step("Check required host ports", lambda: ensure_required_ports_available(cfg)),
            Step("Deploy service", lambda: deploy_service(cfg)),
        ]
        with tqdm(total=len(steps), desc="Deploying service", unit="step") as bar:
            run_steps(steps, bar)
        _print_summary(cfg)
    finally:
        log_line(f"=== END {dt.datetime.now(dt.timezone.utc).isoformat()}Z ===")


def _print_summary(cfg) -> None:
    print()
    print("+----------------------------------------------------+")
    print("| Deployment complete                                |")
    print("+----------------------------------------------------+")
    print()
    print(f"Service name : {cfg.service_name}")
    print(f"Source dir   : {cfg.source_dir}")
    print(f"Source kind  : {cfg.source_kind.value}")
    print(f"Access mode  : {cfg.access_mode.value}")
    print(f"Ingress mode : {cfg.ingress_mode.value}")
    print(f"Project dir  : {cfg.service_dir}")
    print(f"Retries      : {cfg.registry_retries} (backoff {cfg.retry_backoff_seconds}s)")
    print(f"Docker tune  : {'enabled' if cfg.tune_docker_daemon else 'disabled'}")
    if cfg.source_kind.value == "dockerfile":
        compose_file = cfg.managed_compose_path
    else:
        compose_file = cfg.source_compose_path
    print(f"Compose file : {compose_file}")
    if cfg.uses_managed_ingress:
        print(f"Proxy file   : {cfg.managed_proxy_compose_path}")
        if cfg.tls_enabled:
            print(
                f"Proxy ports  : "
                f"{cfg.effective_proxy_http_port}->{cfg.effective_proxy_https_port}"
            )
        else:
            print(f"Proxy port   : {cfg.effective_proxy_http_port}")
    elif cfg.reverse_proxy_enabled:
        print(f"Nginx site   : {cfg.host_nginx_site_available_path}")
        if cfg.tls_enabled:
            print("Proxy ports  : 80->443 (host nginx)")
        else:
            print("Proxy port   : 80 (host nginx)")
    if cfg.tls_enabled:
        print(f"Domain       : {cfg.domain}")
        if len(cfg.cert_domain_names) > 1:
            print(f"TLS domains  : {', '.join(cfg.cert_domain_names)}")
    if cfg.auth_token is not None:
        print("Auth token   : enabled")
    else:
        print("Auth token   : disabled")
    if cfg.reverse_proxy_enabled:
        print(
            f"Proxy target : "
            f"{cfg.effective_proxy_upstream_service}:{cfg.effective_proxy_upstream_port}"
        )
        if cfg.proxy_routes:
            print(
                "Proxy routes : "
                + ", ".join(
                    f"{r.host}{r.path_prefix}->{r.upstream_host}:{r.upstream_port}"
                    for r in cfg.proxy_routes
                )
            )
    if cfg.source_kind.value == "compose":
        if cfg.compose_services:
            print(f"Services     : {', '.join(cfg.compose_services)}")
        else:
            print("Services     : all")
    print()
    print("Useful commands:")
    compose_files = [str(compose_file)]
    if cfg.uses_managed_ingress:
        compose_files.append(str(cfg.managed_proxy_compose_path))
    compose_files_arg = " ".join(f"-f {path}" for path in compose_files)
    services = ""
    if cfg.compose_services and cfg.source_kind.value == "compose":
        services = " " + " ".join(quote(s) for s in cfg.compose_services)
    print(
        "  docker compose "
        f"-p {cfg.compose_project_name} "
        f"{compose_files_arg} ps{services}"
    )
    print(
        "  docker compose "
        f"-p {cfg.compose_project_name} "
        f"{compose_files_arg} logs -f{services}"
    )
    if cfg.tls_enabled and cfg.uses_managed_ingress:
        print(
            "  docker compose "
            f"-p {cfg.compose_project_name} "
            f"{compose_files_arg} run --rm certbot renew && "
            "docker compose "
            f"-p {cfg.compose_project_name} "
            f"{compose_files_arg} exec -T nginx nginx -s reload"
        )
    elif cfg.tls_enabled and cfg.reverse_proxy_enabled:
        print(
            f"  certbot renew && nginx -t && systemctl reload nginx "
            f"# site: {cfg.host_nginx_site_name}"
        )
