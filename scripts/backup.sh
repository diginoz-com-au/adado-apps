#!/bin/bash
set -e
BACKUP_DIR="/mnt/nas/OpenClaw/AdaDo/backups"
DATE=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"
# Backup SQLite DB
docker exec adado-db sqlite3 /data/adado.db ".dump" > "$BACKUP_DIR/adado-db-$DATE.sql" 2>/dev/null ||   docker cp adado-db:/data/adado.db "$BACKUP_DIR/adado-db-$DATE.db" 2>/dev/null && echo "DB backed up"
# Backup core .env (redacted)
grep -v 'KEY\|SECRET\|TOKEN\|PASSWORD' /home/ada/adado/harness/.env > "$BACKUP_DIR/env-config-$DATE.txt" && echo "Config backed up"
echo "Backup complete: $DATE"
