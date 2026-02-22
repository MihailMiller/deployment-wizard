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
- `--compose-service` (repeat for multiple compose services; default is all)
- `--registry-retries`
- `--retry-backoff-seconds`
- `--no-docker-daemon-tuning`
- `--auth-token` (enable bearer-token auth at managed proxy)
- `--domain` + `--certbot-email` (enable nginx + certbot)
- `--proxy-http-port` + `--proxy-https-port` (external nginx bind ports)
- `--proxy-upstream-service` (compose sources)
- `--proxy-upstream-port`

Notes:
- For compose sources, `--access-mode` values other than `localhost` require managed proxy mode (`--domain` or `--auth-token`).
- `--domain` (Let's Encrypt HTTP-01) requires `--access-mode public`.
- Interactive mode proactively checks proxy host port availability and suggests alternatives.

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
