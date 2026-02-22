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
- `--compose-service` (repeat for multiple compose services; default is all)
- `--registry-retries`
- `--retry-backoff-seconds`
- `--no-docker-daemon-tuning`

## Network Hardening

By default, deployment writes Docker daemon settings to improve pull reliability:

- `max-concurrent-downloads = 1`
- `max-concurrent-uploads = 1`
- fallback DNS: `1.1.1.1`, `8.8.8.8` (only if DNS is not already configured)

You can disable this behavior with `--no-docker-daemon-tuning`.

## Development

Run tests:

```bash
python -m unittest discover -s tests -v
```

Compile check:

```bash
python -m compileall -q deploy_wizard
```
