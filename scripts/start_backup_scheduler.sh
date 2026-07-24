#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p /backups/logs
umask 077
export -p > /run/hasanara-backup.env
chmod 0600 /run/hasanara-backup.env

cat > /etc/cron.d/hasanara-backup <<'CRON'
0 2 * * * /bin/bash -c '. /run/hasanara-backup.env; exec /scripts/backup_db.sh' >> /backups/logs/cron.log 2>&1
0 4 * * 0 /bin/bash -c '. /run/hasanara-backup.env; exec /scripts/verify_backup.sh' >> /backups/logs/cron.log 2>&1
*/5 * * * * /bin/bash -c '. /run/hasanara-backup.env; exec /scripts/export_backup_metrics.sh --output-file /backups/metrics.prom' >> /backups/logs/cron.log 2>&1
CRON

if [[ "${MEDIA_BACKUP_ENABLED:-false}" == "true" ]]; then
    cat >> /etc/cron.d/hasanara-backup <<'CRON'
0 3 * * * /bin/bash -c '. /run/hasanara-backup.env; exec /scripts/backup_media.sh' >> /backups/logs/cron.log 2>&1
CRON
fi

if [[ -n "${WALG_S3_PREFIX:-}" ]]; then
    cat >> /etc/cron.d/hasanara-backup <<'CRON'
0 5 * * 0 /bin/bash -c '. /run/hasanara-backup.env; exec /scripts/walg_base_backup.sh' >> /backups/logs/cron.log 2>&1
CRON
fi

chmod 0644 /etc/cron.d/hasanara-backup
crontab /etc/cron.d/hasanara-backup

echo "Backup scheduler started. Logs: /backups/logs/cron.log"
exec cron -f
