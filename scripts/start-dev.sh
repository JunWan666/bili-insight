#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python="${root}/backend/.venv/bin/python"

if [ ! -x "${python}" ]; then
    echo "Backend virtual environment is missing. Run 'make bootstrap' first." >&2
    exit 1
fi
if [ ! -d "${root}/frontend/node_modules" ]; then
    echo "Frontend dependencies are missing. Run 'make bootstrap' first." >&2
    exit 1
fi

runtime="${root}/runtime"
data="${runtime}/data"
artifacts="${runtime}/artifacts"
temporary="${runtime}/temp"
logs="${runtime}/logs"
secrets="${runtime}/secrets"
key_file="${secrets}/cookie-encryption.key"

mkdir -p "${data}" "${artifacts}" "${temporary}" "${logs}" "${secrets}"
if [ ! -s "${key_file}" ]; then
    umask 077
    "${python}" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))" > "${key_file}"
fi

export APP_ENVIRONMENT=development
export APP_HOST=127.0.0.1
export APP_PORT=8000
export APP_NETWORK_MODE=local
export APP_DATABASE_URL="sqlite+aiosqlite:///${data}/bili_insight.db"
export APP_DATA_DIR="${data}"
export APP_ARTIFACT_DIR="${artifacts}"
export APP_TEMP_DIR="${temporary}"
export APP_LOG_DIR="${logs}"
export APP_COOKIE_ENCRYPTION_KEY_FILE="${key_file}"
export APP_LOG_JSON=false
export APP_AUTO_CREATE_SCHEMA=false
export APP_CORS_ORIGINS='http://127.0.0.1:5173,http://localhost:5173'
export VITE_PROXY_TARGET=http://127.0.0.1:8000
export VITE_DEV_HOST="${VITE_DEV_HOST:-127.0.0.1}"

cd "${root}/backend"
"${python}" -m alembic upgrade head
"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
backend_pid=$!

cleanup() {
    kill "${backend_pid}" 2>/dev/null || true
    wait "${backend_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "${root}/frontend"
npm run dev
