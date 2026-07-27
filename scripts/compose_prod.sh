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

compose() {
    "${CLEAN_ENV[@]}" "${COMPOSE[@]}" "$@"
}

run_compose() {
    exec "${CLEAN_ENV[@]}" "${COMPOSE[@]}" "$@"
}

usage() {
    printf '%s\n' 'usage: scripts/compose_prod.sh {preflight|deploy|maintenance|config|ps|logs|top|images|version} [arguments]' >&2
}

is_uuid() {
    [[ $1 =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
}

diarization_requeue_if_running() {
    local video_id=$1
    compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -v video_id="$video_id" \
        -c "UPDATE videos SET diarization_state='pending', diarization_error='Requeued interrupted diarization canary', updated_at=now() WHERE id=:'video_id'::uuid AND diarization_state='running';" >/dev/null || return 1
    compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -v video_id="$video_id" \
        -c "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM videos WHERE id=:'video_id'::uuid AND diarization_state <> 'running') THEN 1 ELSE 0 END;" >/dev/null || return 1
}

check_diarization_role() {
    compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts < scripts/check_diarization_role.sql
}

readonly DIARIZATION_LOCK_NAME='hasanara-diarization-canary'
lock_pid=
lock_backend_pid=
lock_ready=
lock_application_name=

lock_application_name_is_valid() {
    [[ $lock_application_name =~ ^hasanara-diarization-[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ && ${#lock_application_name} -le 63 ]]
}

generate_lock_application_name() {
    local invocation_uuid
    invocation_uuid=$(</proc/sys/kernel/random/uuid) || return 1
    is_uuid "$invocation_uuid" || return 1
    lock_application_name="hasanara-diarization-$invocation_uuid"
    lock_application_name_is_valid || return 1
}

lock_is_alive() {
    [[ -n $lock_pid && -n $lock_backend_pid ]] && kill -0 "$lock_pid" 2>/dev/null || { printf '%s\n' 'diarization canary lock holder died' >&2; return 1; }
    [[ $lock_backend_pid =~ ^[1-9][0-9]*$ ]] || return 1
    lock_application_name_is_valid || return 1
    [[ $(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -v lock_pid="$lock_backend_pid" -v lock_application_name="$lock_application_name" -c "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid WHERE a.pid = :'lock_pid'::integer AND a.application_name = :'lock_application_name' AND l.locktype = 'advisory' AND l.granted) THEN 1 ELSE 0 END;") == 1 ]] || { printf '%s\n' 'diarization canary lock is no longer held by its server backend' >&2; return 1; }
}

release_lock() {
    local release_status=0 proof terminated pid_filter=''
    if [[ -n $lock_application_name ]]; then
        if ! lock_application_name_is_valid; then
            release_status=1
        else
            if [[ -n $lock_backend_pid ]]; then
                if [[ $lock_backend_pid =~ ^[1-9][0-9]*$ ]]; then
                    pid_filter=" AND pid = :'lock_pid'::integer"
                else
                    release_status=1
                fi
            fi
            if ((release_status == 0)); then
                terminated=$(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -v lock_pid="$lock_backend_pid" -v lock_application_name="$lock_application_name" -c "SELECT COALESCE(bool_and(pg_terminate_backend(pid)), true) FROM (SELECT pid FROM pg_stat_activity WHERE application_name = :'lock_application_name'$pid_filter) holders;") || release_status=1
                [[ $terminated == t ]] || release_status=1
            fi
        fi
    fi
    if [[ -n $lock_pid ]]; then kill -TERM "$lock_pid" 2>/dev/null || true; wait "$lock_pid" 2>/dev/null || true; fi
    proof=$(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -c "SELECT 1 / CASE WHEN pg_try_advisory_lock(hashtext('$DIARIZATION_LOCK_NAME')) THEN 1 ELSE 0 END; SELECT 1 / CASE WHEN pg_advisory_unlock(hashtext('$DIARIZATION_LOCK_NAME')) THEN 1 ELSE 0 END;") || release_status=1
    [[ $proof == $'1\n1' ]] || release_status=1
    [[ -z $lock_ready ]] || rm -f -- "$lock_ready" || release_status=1
    lock_pid=; lock_backend_pid=; lock_ready=; lock_application_name=
    ((release_status == 0)) || printf '%s\n' 'cannot release diarization canary lock cleanly' >&2
    return "$release_status"
}

start_lock_holder() {
    lock_application_name_is_valid || return 1
    lock_ready=$(mktemp)
    compose exec -T -e "PGAPPNAME=$lock_application_name" db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -c "SELECT pg_backend_pid(); SELECT 1 / CASE WHEN pg_try_advisory_lock(hashtext('$DIARIZATION_LOCK_NAME')) THEN 1 ELSE 0 END; SELECT 'locked'; SELECT pg_sleep(2147483647);" >"$lock_ready" &
    lock_pid=$!
}

await_lock_holder() {
    local output _
    for _ in 1 2 3 4 5; do output=$(<"$lock_ready"); [[ $output == *locked* ]] && break; sleep 1; done
    [[ $output =~ ^([1-9][0-9]*)$'\n'1$'\n'locked$ ]] || { printf '%s\n' 'diarization canary lock is unavailable' >&2; return 1; }
    lock_backend_pid=${BASH_REMATCH[1]}
    lock_is_alive
}

guarded_diarization_canary_cleanup() {
    local video_id=$1 name="hasanara-diarization-canary-$1" details container_id container_name project service oneoff ids
    ids=$(timeout 10s "${CLEAN_ENV[@]}" docker container ls --all --no-trunc --filter "name=^/${name}$" --format '{{.ID}}') || { printf '%s\n' 'cannot inspect diarization canary container' >&2; return 1; }
    [[ -z $ids ]] && return 0
    [[ $ids != *$'\n'* && $ids =~ ^[0-9a-f]{64}$ ]] || { printf '%s\n' 'unsafe diarization canary container list' >&2; return 1; }
    details=$(timeout 10s "${CLEAN_ENV[@]}" docker container inspect --format '{{.Id}}|{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.oneoff"}}' "$ids") || { printf '%s\n' 'cannot inspect diarization canary container' >&2; return 1; }
    IFS='|' read -r container_id container_name project service oneoff <<<"$details"
    [[ $container_id == "$ids" && $container_id =~ ^[0-9a-f]{64}$ && $container_name == "/$name" && $project == hasanara && $service == diarization-worker && $oneoff == True ]] || {
        printf '%s\n' 'refusing unsafe diarization canary cleanup' >&2; return 1;
    }
    timeout 30s "${CLEAN_ENV[@]}" docker container stop --time 20 "$container_id" >/dev/null || return 1
    timeout 10s "${CLEAN_ENV[@]}" docker container rm --force "$container_id" >/dev/null || return 1
    ids=$(timeout 10s "${CLEAN_ENV[@]}" docker container ls --all --no-trunc --filter "name=^/${name}$" --format '{{.ID}}') || return 1
    [[ -z $ids ]] || { printf '%s\n' 'diarization canary container remains after cleanup' >&2; return 1; }
}

emergency_diarization_canary_cleanup() {
    local video_id=$1 status=0
    finish_emergency_cleanup() {
        local original_status=$1 release_status=0
        release_lock || release_status=1
        ((original_status == 0 && release_status == 0))
    }
    on_emergency_exit() { local exit_status=$?; trap - EXIT INT TERM; finish_emergency_cleanup "$exit_status" || exit 1; exit "$exit_status"; }
    on_emergency_signal() { trap - EXIT INT TERM; finish_emergency_cleanup 1 || exit 1; exit "$1"; }
    trap on_emergency_exit EXIT
    trap 'on_emergency_signal 130' INT
    trap 'on_emergency_signal 143' TERM
    generate_lock_application_name
    start_lock_holder
    await_lock_holder || status=1
    if ((status == 0)); then guarded_diarization_canary_cleanup "$video_id" || status=1; fi
    if ((status == 0)); then lock_is_alive || status=1; fi
    if ((status == 0)); then diarization_requeue_if_running "$video_id" || status=1; fi
    if ((status == 0)); then lock_is_alive || status=1; fi
    trap - EXIT INT TERM
    release_lock || status=1
    return "$status"
}

run_diarization_canary() {
    local video_id=$1 name="hasanara-diarization-canary-$1" worker_pid= wav_path= before_queue= after_queue= status=0 cleaned=false
    cleanup_canary() {
        lock_is_alive || return 1
        [[ $cleaned == true ]] && return 0
        guarded_diarization_canary_cleanup "$video_id" || return 1
        lock_is_alive || return 1
        diarization_requeue_if_running "$video_id" || return 1
        lock_is_alive || return 1
        cleaned=true
    }
    terminate_worker() {
        local _
        [[ -n $worker_pid ]] || return 0
        if kill -0 "$worker_pid" 2>/dev/null; then
            kill -TERM "$worker_pid" || return 1
            for _ in {1..30}; do
                kill -0 "$worker_pid" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$worker_pid" 2>/dev/null; then
                kill -KILL "$worker_pid" || return 1
            fi
        fi
        wait "$worker_pid" || :
        worker_pid=
    }
    finish_canary() {
        local original_status=$1 termination_status=0 cleanup_status=0 release_status=0
        terminate_worker || termination_status=1
        cleanup_canary || cleanup_status=1
        release_lock || release_status=1
        ((termination_status == 0 && cleanup_status == 0 && release_status == 0 && original_status == 0))
    }
    on_exit() { local status=$?; trap - EXIT INT TERM; finish_canary "$status" || exit 1; exit "$status"; }
    on_signal() { trap - EXIT INT TERM; finish_canary 1 || exit 1; exit "$1"; }
    run_preflight
    check_diarization_role
    trap on_exit EXIT
    trap 'on_signal 130' INT
    trap 'on_signal 143' TERM
    generate_lock_application_name
    start_lock_holder
    await_lock_holder || return 1
    # All eligibility, WAV, and file checks occur only after the cross-host lock.
    lock_is_alive
    compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -v video_id="$video_id" -c "SELECT 1 / CASE WHEN EXISTS (SELECT 1 FROM videos v WHERE v.id=:'video_id'::uuid AND v.state='completed' AND v.diarization_state='pending' AND v.wav_path IS NOT NULL AND v.duration_seconds IS NOT NULL AND v.duration_seconds <= 600 AND EXISTS (SELECT 1 FROM segments s WHERE s.video_id=v.id)) THEN 1 ELSE 0 END;" >/dev/null
    lock_is_alive
    wav_path=$(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -v video_id="$video_id" -c "SELECT wav_path FROM videos WHERE id=:'video_id'::uuid;")
    lock_is_alive
    [[ $wav_path == /data/* && $wav_path != *$'\n'* ]] || { printf '%s\n' 'diarization canary WAV path is invalid' >&2; return 1; }
    lock_is_alive
    compose --profile diarization run --rm --no-deps --entrypoint test diarization-worker -f "$wav_path"
    lock_is_alive
    before_queue=$(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -v video_id="$video_id" -c "SELECT count(*)::text || '|' || md5(COALESCE(string_agg(id::text || ':' || state || ':' || diarization_state || ':' || COALESCE(diarization_error, '') || ':' || updated_at::text, ',' ORDER BY id), '')) FROM videos WHERE id <> :'video_id'::uuid;")
    lock_is_alive
    timeout --signal=TERM --kill-after=30s 20m "${CLEAN_ENV[@]}" "${COMPOSE[@]}" --profile diarization run --rm --no-deps --name "$name" \
        -e "DIARIZATION_ALLOWED_VIDEO_IDS=$video_id" \
        -e DIARIZATION_REQUIRE_ALLOWLIST=true \
        -e DIARIZATION_MAX_JOBS_PER_PROCESS=1 \
        -e DIARIZATION_EXIT_WHEN_IDLE=true \
        -e DIARIZATION_MAX_DURATION_SECONDS=600 \
        -e DIARIZATION_DEVICE=cpu \
        -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e DIARIZATION_STRICT=true \
        diarization-worker &
    worker_pid=$!
    while kill -0 "$worker_pid" 2>/dev/null; do
        if ! lock_is_alive; then
            terminate_worker || return 1
            return 1
        fi
        sleep 2
    done
    wait "$worker_pid" || status=$?
    worker_pid=
    lock_is_alive || return 1
    cleanup_canary || return 1
    lock_is_alive || return 1
    after_queue=$(compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -At -v video_id="$video_id" -c "SELECT count(*)::text || '|' || md5(COALESCE(string_agg(id::text || ':' || state || ':' || diarization_state || ':' || COALESCE(diarization_error, '') || ':' || updated_at::text, ',' ORDER BY id), '')) FROM videos WHERE id <> :'video_id'::uuid;") || status=1
    lock_is_alive || return 1
    [[ $before_queue == "$after_queue" ]] || status=1
    if ((status == 0)); then
        lock_is_alive || return 1
        compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -v video_id="$video_id" -c "SELECT 1 / CASE WHEN (SELECT diarization_state='completed' FROM videos WHERE id=:'video_id'::uuid) AND (SELECT count(*) FROM segments WHERE video_id=:'video_id'::uuid AND speaker_label IS NOT NULL) > 0 AND (SELECT count(DISTINCT speaker_label) FROM segments WHERE video_id=:'video_id'::uuid AND speaker_label IS NOT NULL) BETWEEN 1 AND 20 THEN 1 ELSE 0 END;" >/dev/null || status=$?
        lock_is_alive || return 1
    fi
    trap - EXIT INT TERM
    release_lock || return 1
    [[ $status -eq 0 && $cleaned == true ]]
}

[[ ${BASH_SOURCE[0]} == "$0" ]] || return 0

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
        if [[ ${1:-} == diarization-canary ]]; then
            if (($# != 3)) || ! is_uuid "${2:-}" || [[ ${3:-} != --approved ]]; then
                printf '%s\n' 'diarization-canary requires exactly one UUID and --approved' >&2
                exit 64
            fi
            run_diarization_canary "$2"
            exit $?
        fi
        if [[ ${1:-} == diarization-canary-cleanup ]]; then
            if (($# != 3)) || ! is_uuid "${2:-}" || [[ ${3:-} != --approved ]]; then
                printf '%s\n' 'diarization-canary-cleanup requires exactly one UUID and --approved' >&2
                exit 64
            fi
            emergency_diarization_canary_cleanup "$2"
            exit $?
        fi
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
                run_compose exec db psql -v ON_ERROR_STOP=1 -U postgres -d transcripts -c "SELECT version_num = '20260714_0300' AS at_expected_head FROM alembic_version; SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sessions' AND column_name = 'token') AS hash_only_sessions; SELECT 1 / CASE WHEN (SELECT version_num = '20260714_0300' FROM alembic_version) AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sessions' AND column_name = 'token') THEN 1 ELSE 0 END AS verification_must_be_1;"
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
            diarization-role-check)
                run_preflight
                check_diarization_role
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
