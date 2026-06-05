#!/bin/bash
# deploy.sh — Розгортання radacleaner на сервері

set -e

REMOTE_HOST="${1:-192.168.1.221}"
REMOTE_USER="${2:-test-agent}"
REMOTE_DIR="/home/${REMOTE_USER}/radacleaner"

echo "=== Deploying radacleaner to ${REMOTE_HOST} ==="

# Створюємо директорію на сервері
ssh ${REMOTE_USER}@${REMOTE_HOST} "mkdir -p ${REMOTE_DIR}/src ${REMOTE_DIR}/migrations ${REMOTE_DIR}/tests"

# Копіюємо файли
rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    ./ ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/

# Встановлюємо залежності
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd ${REMOTE_DIR} && pip3 install -r requirements.txt"

# Ініціалізуємо БД (якщо ще не створена)
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd ${REMOTE_DIR} && psql -h \${DB_HOST:-192.168.1.229} -U \${DB_USER:-hermes} -d \${DB_NAME:-my_bills} -f migrations/001_initial.sql 2>/dev/null || echo 'DB already initialized'"

# Налаштовуємо cron
ssh ${REMOTE_USER}@${REMOTE_HOST} "crontab -l 2>/dev/null | grep -v 'radacleaner' | { cat; echo '55 * * * * cd ${REMOTE_DIR} && python3 -m src.sync_bills list >> /tmp/sync.log 2>&1'; echo '0 * * * * cd ${REMOTE_DIR} && python3 -m src.rag_monitor >> /tmp/rag_monitor.log 2>&1'; echo '0 3 * * * cd ${REMOTE_DIR} && python3 -m src.sync_bills full >> /tmp/sync_full.log 2>&1'; } | crontab -"

echo "=== Deploy complete ==="
echo "Don't forget to create .env file on the server!"
