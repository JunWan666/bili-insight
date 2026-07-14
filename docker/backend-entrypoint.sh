#!/bin/sh
set -eu

umask 077

if [ "${GENERATE_COOKIE_KEY:-false}" = "true" ]; then
    key_file="${APP_COOKIE_ENCRYPTION_KEY_FILE:?APP_COOKIE_ENCRYPTION_KEY_FILE is required when GENERATE_COOKIE_KEY=true}"
    if [ ! -s "${key_file}" ]; then
        key_dir=$(dirname "${key_file}")
        mkdir -p "${key_dir}"
        temporary_key="${key_file}.tmp.$$"
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))" > "${temporary_key}"
        chmod 0600 "${temporary_key}"
        mv "${temporary_key}" "${key_file}"
        echo "Cookie encryption key initialized in the dedicated secrets volume."
    fi
fi

mkdir -p \
    "${APP_DATA_DIR:-/app/runtime/data}" \
    "${APP_ARTIFACT_DIR:-/app/runtime/artifacts}" \
    "${APP_TEMP_DIR:-/app/runtime/temp}" \
    "${APP_LOG_DIR:-/app/runtime/logs}"

if [ "${RUN_DATABASE_MIGRATIONS:-true}" = "true" ]; then
    python -m alembic upgrade head
fi

exec "$@"
