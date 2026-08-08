#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

MSG="${1:-WIP safe commit}"

git status -sb

echo ""
echo "Agregando solo archivos seguros..."
git add .gitignore .vscode scripts web backend 2>/dev/null || true

echo ""
echo "Sacando del stage archivos sensibles por seguridad..."
git reset -- .env .env.* '*.pem' '*.key' id_rsa id_rsa.* backups logs 2>/dev/null || true

echo ""
echo "Commit..."
git commit -m "$MSG"
