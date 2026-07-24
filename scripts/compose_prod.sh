#!/usr/bin/env bash
# Run the exact HasanAra production stack without inherited shell variables.
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

readonly -a CLEAN_ENV=(env -i PATH="$PATH" HOME="$HOME" USER="${USER:-}" HASANARA_ENV_FILE=.env.prod)
readonly -a COMPOSE=(docker compose
    --project-name hasanara
    --env-file .env.prod
    --file docker-compose.yml
    --file docker-compose.gtx1080.yml
    --file docker-compose.hasanara.yml
    --file docker-compose.storage.yml
    --file docker-compose.pitr.yml
    --file docker-compose.release.yml)

run_preflight() {
    "${CLEAN_ENV[@]}" python3 scripts/release_preflight.py
}

run_retirement_preflight() {
    "${CLEAN_ENV[@]}" python3 scripts/release_preflight.py --allow-disabled-profile-services
}

run_compose() {
    exec "${CLEAN_ENV[@]}" "${COMPOSE[@]}" "$@"
}

usage() {
    printf '%s\n' 'usage: scripts/compose_prod.sh {preflight|deploy|maintenance|config|ps|logs|top|images|version} [arguments]' >&2
}

command=${1:-}
case "$command" in
    preflight)
        shift
        exec "${CLEAN_ENV[@]}" python3 scripts/release_preflight.py "$@"
        ;;
    deploy)
        shift
        if (($# != 0)); then
            usage
            exit 64
        fi
        run_preflight
        run_compose up -d --no-build --pull always
        ;;
    maintenance)
        shift
        if (($# != 2)) || [[ ${2:-} != --approved ]]; then
            printf '%s\n' 'maintenance requires an approved fixed action' >&2
            exit 64
        fi
        case "$1" in
            retire-disabled-profiles)
                run_retirement_preflight
                run_compose --profile full --profile diarization rm --stop --force opensearch dashboards prometheus grafana diarization-worker
                ;;
            session-token-contract-drain)
                run_preflight
                run_compose stop api worker analytics-retention summary-refresher archive-intelligence-refresher diarization-worker
                ;;
            session-token-contract-inspect)
                run_preflight
                run_compose exec db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -c 'SELECT pid, usename, application_name, state, wait_event_type, query_start, xact_start, left(query, 120) AS query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() ORDER BY xact_start NULLS LAST, query_start NULLS LAST;'
                ;;
            session-token-contract-migration)
                run_preflight
                run_compose run --rm -e ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION=true migrations
                ;;
            session-token-contract-verify)
                run_preflight
                run_compose exec db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -c "SELECT version_num = '20260714_0200' AS at_expected_head FROM alembic_version; SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sessions' AND column_name = 'token') AS hash_only_sessions; SELECT 1 / CASE WHEN (SELECT version_num = '20260714_0200' FROM alembic_version) AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sessions' AND column_name = 'token') THEN 1 ELSE 0 END AS verification_must_be_1;"
                ;;
            pitr-base-backup)
                run_preflight
                run_compose exec backup /scripts/walg_base_backup.sh
                ;;
            pitr-switch-wal)
                run_preflight
                run_compose exec db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -c 'SELECT pg_switch_wal();'
                ;;
            pitr-archive-status)
                run_preflight
                run_compose exec db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -c 'SELECT archived_count, failed_count, last_archived_wal, last_archived_time, last_failed_wal, last_failed_time, stats_reset FROM pg_stat_archiver;'
                ;;
            pitr-list-backups)
                run_preflight
                run_compose exec backup wal-g backup-list
                ;;
            *)
                printf '%s\n' 'maintenance requires an approved fixed action' >&2
                exit 64
                ;;
        esac
        ;;
    config)
        shift
        if (($# != 1)); then
            printf '%s\n' 'config requires exactly one safe selector' >&2
            exit 64
        fi
        case "$1" in
            --quiet|--services|--profiles|--images) ;;
            *)
                printf '%s\n' 'config requires exactly one safe selector' >&2
                exit 64
                ;;
        esac
        run_compose config "$1"
        ;;
    ps|logs|top|images|version)
        shift
        run_compose "$command" "$@"
        ;;
    up|down|pull|build|create|start|restart|stop|rm|run|exec|kill)
        printf '%s\n' "refusing unguarded state-changing command: $command; use deploy or maintenance" >&2
        exit 64
        ;;
    *)
        usage
        exit 64
        ;;
esac
