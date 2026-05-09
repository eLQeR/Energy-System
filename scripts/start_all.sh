#!/usr/bin/env bash
# Піднімає всю систему у Docker. Нічого на хості окрім docker не треба.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ docker compose up -d --build"
docker compose up -d --build

cat <<EOF

Готово. Перевір:
  Grafana          http://localhost:3000     (admin/admin)
  Fuseki           http://localhost:3030     (admin/admin)
  Ontology API     http://localhost:5002/devices
  Edge panel       http://localhost:5001
  InfluxDB UI      http://localhost:8087     (admin/adminadmin)

Логи окремого сервісу:   docker compose logs -f <service>
  (сервіси: collector, mqtt_bridge, ontology_api, analyzer_panel,
            ontology_loader, mosquitto, influxdb, fuseki, grafana)

Зупинка:                 scripts/stop_all.sh
EOF
