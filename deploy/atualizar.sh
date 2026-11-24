#!/usr/bin/env bash
# Atualiza o servidor com a última versão da branch e reinicia (os dados ficam no volume "data").
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose -f docker-compose.deploy.yml up -d --build
docker image prune -f >/dev/null
echo "Atualizado. Versão: $(grep -o '"[0-9.]*-[a-z]*"' app/__init__.py)"
