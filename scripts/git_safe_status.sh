#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== rama ==="
git branch --show-current

echo ""
echo "=== estado ==="
git status -sb

echo ""
echo "=== archivos con posible secreto NO commitear ==="
grep -RIlE 'EAA[A-Za-z0-9_-]{20,}|DATABASE_URL=.+|WHATSAPP_ACCESS_TOKEN=.+|WHATSAPP_WEBHOOK_VERIFY_TOKEN=.+|BEGIN RSA|BEGIN OPENSSH|PRIVATE KEY|ghp_[A-Za-z0-9_]+' \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=venv \
  --exclude-dir=node_modules \
  --exclude-dir=backups \
  --exclude='*.bak*' \
  . 2>/dev/null || true
