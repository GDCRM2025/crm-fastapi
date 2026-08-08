#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/greendiamond/crm"
VENV_DIR="/opt/greendiamond/venv"
ENV_FILE="${APP_DIR}/.env"
DEPLOY_DIR="${APP_DIR}/deploy/ubuntu"
DB_ROLE="crm_gd"
DB_NAME="crm_gd"
DB_SECRET_DIR="/etc/greendiamond"
DB_SECRET_FILE="${DB_SECRET_DIR}/crm-db-password"
SERVER_IP="${CRM_SERVER_IP:-192.168.10.51}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta este script con sudo." >&2
  exit 1
fi

for required in "${APP_DIR}" "${VENV_DIR}/bin/uvicorn" "${DEPLOY_DIR}/crm-gd.service" "${DEPLOY_DIR}/crm-gd.nginx"; do
  if [[ ! -e "${required}" ]]; then
    echo "Falta: ${required}" >&2
    exit 1
  fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  curl \
  nginx \
  openssl \
  postgresql \
  postgresql-contrib \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libharfbuzz0b \
  libharfbuzz-subset0 \
  libgdk-pixbuf-2.0-0 \
  libffi8 \
  fonts-dejavu-core \
  fonts-liberation

install -d -m 0700 "${DB_SECRET_DIR}"
if [[ ! -s "${DB_SECRET_FILE}" ]]; then
  openssl rand -hex 32 > "${DB_SECRET_FILE}"
fi
chmod 0600 "${DB_SECRET_FILE}"
DB_PASSWORD="$(tr -d '\r\n' < "${DB_SECRET_FILE}")"

systemctl enable --now postgresql

runuser -u postgres -- psql -v ON_ERROR_STOP=1 \
  --set=db_role="${DB_ROLE}" \
  --set=db_name="${DB_NAME}" \
  --set=db_password="${DB_PASSWORD}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'db_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_role')
\gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'db_role', :'db_password')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0',
              :'db_name', :'db_role', 'UTF8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'db_role')
\gexec
SQL

touch "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"

set_env() {
  local key="$1"
  local value="$2"
  sed -i "/^${key}=/d" "${ENV_FILE}"
  printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
}

if ! grep -q '^JWT_SECRET=.\+' "${ENV_FILE}"; then
  set_env "JWT_SECRET" "$(openssl rand -hex 48)"
fi
set_env "DATABASE_URL" "postgresql+psycopg://${DB_ROLE}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
set_env "APP_URL" "http://${SERVER_IP}/crm"
set_env "DEV_NO_AUTH" "false"
set_env "CRM_DB_POOL_SIZE" "5"
set_env "CRM_DB_MAX_OVERFLOW" "5"
set_env "CRM_DB_POOL_TIMEOUT" "10"

chown oscar:oscar "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"

install -o root -g root -m 0644 "${DEPLOY_DIR}/crm-gd.service" /etc/systemd/system/crm-gd.service
install -o root -g root -m 0644 "${DEPLOY_DIR}/crm-gd.nginx" /etc/nginx/sites-available/crm-gd
ln -sfn /etc/nginx/sites-available/crm-gd /etc/nginx/sites-enabled/crm-gd
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable crm-gd.service
systemctl reload nginx

echo "BOOTSTRAP_OK"
echo "Base PostgreSQL creada: ${DB_NAME}"
echo "Servicio registrado: crm-gd.service (aún no iniciado)"
