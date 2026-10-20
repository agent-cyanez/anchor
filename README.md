# Anchor

[![CI](https://github.com/agent-cyanez/anchor/actions/workflows/ci.yml/badge.svg)](https://github.com/agent-cyanez/anchor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/agent-cyanez/anchor)](https://github.com/agent-cyanez/anchor/releases)
[![Container](https://img.shields.io/badge/ghcr.io-anchor-blue)](https://ghcr.io/agent-cyanez/anchor)

Backup freshness monitor with [ntfy](https://ntfy.sh) alerts. Watches backup files and directories, alerts when they go stale or missing.

**Zero dependencies.** Single Python file, stdlib only. Docker-native.

Part of the [Harbor](https://github.com/agent-cyanez/harbor) self-hosted monitoring suite:
[Lookout](https://github.com/agent-cyanez/lookout) (container lifecycle) · [Beacon](https://github.com/agent-cyanez/beacon) (status page) · [Bosun](https://github.com/agent-cyanez/bosun) (log alerts) · [Sextant](https://github.com/agent-cyanez/sextant) (TLS certs) · [Drift](https://github.com/agent-cyanez/drift) (image updates) · **Anchor** (backup freshness)

## Quick Start

```yaml
services:
  anchor:
    image: ghcr.io/agent-cyanez/anchor
    volumes:
      - /path/to/backups:/backups:ro
    environment:
      BACKUP_TARGETS: "daily-db:/backups/db:24h,weekly-photos:/backups/photos:168h"
      NTFY_URL: http://ntfy:80
      NTFY_TOPIC: backups
```

## How It Works

Anchor periodically checks the modification time of backup files and directories. If a backup hasn't been updated within its expected interval, Anchor sends an alert via ntfy.

- **Files:** Checks the file's modification time directly
- **Directories:** Finds the newest file recursively and checks its modification time
- **Missing paths:** Alerts immediately if the path doesn't exist

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_TARGETS` | *(required)* | Comma-separated targets: `label:path:max_age` |
| `NTFY_URL` | `http://localhost:80` | ntfy server URL |
| `NTFY_TOPIC` | `backups` | ntfy topic for alerts |
| `NTFY_MAX_PRIORITY` | *(none)* | Ceiling for alert priority (`min`, `low`, `default`, `high`, `urgent`). Set to `default` to keep high/urgent free for messages a human must see |
| `CHECK_INTERVAL` | `3600` | Seconds between checks |
| `ALERT_COOLDOWN` | `86400` | Seconds before re-alerting for same issue |
| `HEALTH_PORT` | `8586` | HTTP port for health/API endpoint |

### Target Format

Each target is `label:path:max_age` where:
- **label** — human-readable name (e.g. `daily-db`, `weekly-photos`)
- **path** — file or directory to check (must be mounted in the container)
- **max_age** — maximum acceptable age: `30m`, `24h`, `7d`, or seconds

### Examples

```bash
# Single daily database backup
BACKUP_TARGETS="postgres:/backups/db/latest.sql.gz:24h"

# Multiple schedules
BACKUP_TARGETS="db:/backups/db:24h,photos:/backups/photos:168h,config:/backups/config:48h"

# Hourly log archives
BACKUP_TARGETS="logs:/backups/logs:2h"
```

## API

### `GET /health`

Returns `ok` — for Docker healthcheck and Beacon integration.

### `GET /api/status`

Returns JSON with current backup status:

```json
{
  "targets": [
    {
      "label": "daily-db",
      "path": "/backups/db",
      "status": "fresh",
      "message": "backup is 3h 15m old",
      "max_age": 86400,
      "age": 11700
    }
  ],
  "checked_at": "2026-08-17T14:00:00Z"
}
```

Status values: `fresh` (within max_age), `stale` (exceeded max_age), `missing` (path not found).

## License

MIT
