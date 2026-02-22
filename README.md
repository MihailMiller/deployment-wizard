# Service Deployment Wizard

Generic deployment wizard for Docker microservices on Ubuntu.

It deploys a service from a local directory that contains either:
- `docker-compose.yml` / `compose.yml`
- `Dockerfile` (a compose file is generated automatically)

## Features

- Interactive wizard and batch mode
- Service-name based deployment isolation via Docker Compose project names
- Auto-detect compose vs Dockerfile sources
- Idempotent host bootstrap for Ubuntu + Docker

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

## Development

Run tests:

```bash
python -m unittest discover -s tests -v
```

Compile check:

```bash
python -m compileall -q deploy_wizard
```
