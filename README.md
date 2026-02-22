# Service Deployment Wizard

Generic deployment wizard for Docker microservices on Ubuntu.

It deploys a service from a local directory that contains either:
- `docker-compose.yml` / `compose.yml`
- `Dockerfile` (a compose file is generated automatically)

## Features

- Interactive wizard and batch mode
- Service-name based deployment isolation via Docker Compose project names
- Auto-detect compose vs Dockerfile sources
- Compose service multi-select (deploy all or chosen services)
- Access modes: `localhost`, `tailscale`, `public`
- Optional bearer-token authentication at managed nginx proxy
- Optional nginx reverse proxy with Let's Encrypt (certbot)
- Idempotent host bootstrap for Ubuntu + Docker
- Automatic retry for transient Docker registry/network failures
- Docker daemon network tuning to reduce `connection reset by peer` pull errors

## Quick Start

### Interactive

```bash
sudo python -m deploy_wizard
```

### Batch

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name my-service \
  --source-dir /path/to/service
```

If the source directory only has a `Dockerfile`, a managed compose file is written to:

```text
/opt/services/<service-name>/docker-compose.generated.yml
```

## CLI

```bash
python -m deploy_wizard deploy --batch --help
```

Key flags:
- `--service-name`
- `--source-dir`
- `--source-kind {auto,compose,dockerfile}`
- `--base-dir`
- `--host-port` + `--container-port` (for Dockerfile mode)
- `--bind-host`
- `--access-mode {localhost,tailscale,public}`
- `--ingress-mode {managed,external-nginx,takeover}`
- `--compose-service` (repeat for multiple compose services; default is all)
- `--registry-retries`
- `--retry-backoff-seconds`
- `--no-docker-daemon-tuning`
- `--auth-token` (enable bearer-token auth at managed proxy)
- `--domain` + `--certbot-email` (enable nginx + certbot)
- `--proxy-http-port` + `--proxy-https-port` (external nginx bind ports)
- `--proxy-route HOST[/PATH]=UPSTREAM:PORT` (repeat for multi-host/path routing)
- `--proxy-upstream-service` (compose sources)
- `--proxy-upstream-port`

Notes:
- For compose sources, `--access-mode` values other than `localhost` require proxy mode (`--domain` or `--auth-token`).
- `--domain` (Let's Encrypt HTTP-01) requires `--access-mode public`.
- `--ingress-mode managed` keeps nginx+certbot inside Docker (default).
- `--ingress-mode external-nginx` writes and reloads a host nginx site.
- `--ingress-mode takeover` stops host nginx before reconfigure and starts it again.
- Interactive mode auto-selects free proxy ports (prefers 80/443) and only falls back to manual entry if needed.
- Interactive mode suggests default routes from selected compose services; when deploying all compose services, it only auto-suggests routes for services with published host `ports:`.
- For public TLS in interactive mode, the wizard requires host ports `80/443` by default and asks before switching to non-standard ports.

TLS reverse proxy example:

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name my-service \
  --source-dir /path/to/service \
  --access-mode public \
  --auth-token supersecret-token \
  --proxy-upstream-port 8080
```

Public TLS reverse proxy example:

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name my-service \
  --source-dir /path/to/service \
  --access-mode public \
  --domain api.example.com \
  --certbot-email ops@example.com \
  --proxy-upstream-port 8080
```

Multi-host reverse proxy routes example:

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name llm-stack \
  --source-dir /path/to/compose-source \
  --source-kind compose \
  --access-mode public \
  --domain ingress.example.com \
  --certbot-email ops@example.com \
  --proxy-route wiki.example.com=orchestrator:8090 \
  --proxy-route sickbeard.example.com=media:8081 \
  --proxy-route mail.example.com=mailer:4000
```

Single host, multi-service path routes example:

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name llm-stack \
  --source-dir /path/to/compose-source \
  --source-kind compose \
  --access-mode public \
  --domain apps.example.com \
  --certbot-email ops@example.com \
  --proxy-route apps.example.com/workflow-studio=workflow-studio:8000 \
  --proxy-route apps.example.com/orchestrator=orchestrator:8080 \
  --proxy-route apps.example.com/logbook=logbook:8010
```

Host nginx integration example (no dockerized nginx):

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name llm-stack \
  --source-dir /path/to/compose-source \
  --source-kind compose \
  --access-mode public \
  --ingress-mode external-nginx \
  --domain ingress.example.com \
  --certbot-email ops@example.com \
  --proxy-route studio.example.com=127.0.0.1:8000 \
  --proxy-route api.example.com=127.0.0.1:8080 \
  --proxy-route logs.example.com=127.0.0.1:8010
```

Tailscale-only example (no public exposure):

```bash
sudo python -m deploy_wizard deploy --batch \
  --service-name my-service \
  --source-dir /path/to/service \
  --source-kind dockerfile \
  --host-port 18080 \
  --container-port 8080 \
  --access-mode tailscale
```

## Network Hardening

By default, deployment writes Docker daemon settings to improve pull reliability:

- `max-concurrent-downloads = 1`
- `max-concurrent-uploads = 1`
- fallback DNS: `1.1.1.1`, `8.8.8.8` (only if DNS is not already configured)

You can disable this behavior with `--no-docker-daemon-tuning`.

## Troubleshooting: `Temporary failure in name resolution`

If image pulls work but `pip install`/`apk add` fails inside build or run steps with DNS/network errors,
the Docker host firewall may be blocking container egress (common with UFW forwarding defaults).

Typical symptoms:
- `Temporary failure in name resolution`
- Alpine `apk` warnings fetching indexes, followed by package "no such package"

Quick check:

```bash
docker run --rm alpine:3.20 sh -c \
  "nslookup pypi.org && wget -S -O /dev/null https://pypi.org/simple/"
```

If this fails, apply host-side UFW + forwarding fixes (replace `eth0` with your uplink interface):

```bash
sudo ufw allow out 53/udp
sudo ufw allow out 53/tcp
sudo ufw allow out 443/tcp
sudo ufw route allow in on docker0 out on eth0
sudo sed -i 's/^DEFAULT_FORWARD_POLICY=.*/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
sudo sysctl -w net.ipv4.ip_forward=1
sudo ufw reload
sudo systemctl restart docker
```

If DNS resolution still fails inside containers, set Docker daemon DNS and rebuild:

```bash
sudo mkdir -p /etc/docker
cat <<'EOF' | sudo tee /etc/docker/daemon.json
{
  "dns": ["1.1.1.1", "8.8.8.8"]
}
EOF
sudo systemctl restart docker
```

## Development

Run tests:

```bash
python -m unittest discover -s tests -v
```

Compile check:

```bash
python -m compileall -q deploy_wizard
```
