#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ docker compose down"
docker compose down
