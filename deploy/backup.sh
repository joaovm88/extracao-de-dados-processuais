#!/usr/bin/env bash
# Cópia dos dados (casos, PDFs, relatórios) para ./backups/data-AAAA-MM-DD.tgz
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose -f docker-compose.deploy.yml run --rm --no-deps -v "$PWD/backups:/backup" app \
  sh -c "tar czf /backup/data-$(date +%F).tgz -C /data ."
ls -lh backups | tail -3
