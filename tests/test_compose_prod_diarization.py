"""Execution-level guards for the production diarization canary shell helper."""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compose_prod.sh"
VIDEO_ID = "12345678-1234-4234-8234-123456789abc"


def _run_canary(
    tmp_path: Path,
    *,
    lock: str = "healthy",
    docker_mode: str = "absent",
    requeue_fails: bool = False,
    interrupt_before_pid: bool = False,
):
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> {log!s}
mode={docker_mode}
if [[ $* == *'container ls'* ]]; then
    [[ $mode == daemon_fail ]] && exit 1
    if [[ $mode == present* || $mode == replacement ]]; then
        if [[ ! -f {tmp_path / 'listed_once'} ]]; then
            : > {tmp_path / 'listed_once'}
            printf '%064d\\n' 0 | tr '0' a
        fi
    fi
    exit 0
fi
if [[ $* == *'container inspect'* ]]; then
    if [[ $mode == replacement ]]; then
        printf '%064d|/hasanara-diarization-canary-{VIDEO_ID}|hasanara|diarization-worker|True\\n' 0 | tr '0' b
    else
        printf '%064d|/hasanara-diarization-canary-{VIDEO_ID}|hasanara|diarization-worker|True\\n' 0 | tr '0' a
    fi
    exit 0
fi
if [[ $* == *'container stop'* && $mode == *stop_fail ]]; then exit 1; fi
if [[ $* == *'container rm'* && $mode == *rm_fail ]]; then exit 1; fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    holder_tail = (
        'kill -TERM "$CANARY_PID"; exec sleep 3'
        if interrupt_before_pid
        else "[[ ${LOCK_MODE:-healthy} == healthy ]] && exec sleep 8; return 0"
    )
    code = f"""
source {SCRIPT!s}
run_preflight() {{ :; }}
check_diarization_role() {{ :; }}
compose() {{
    printf 'compose %s\\n' "$*" >> "$CANARY_LOG"
    if {str(requeue_fails).lower()}; then
        for argument in "$@"; do
            [[ $argument == "UPDATE videos SET"* ]] && return 1
        done
    fi
    case " $* " in
        *pg_sleep*) [[ ${{LOCK_MODE:-healthy}} == unavailable ]] && return 1; printf '4321\\n1\\nlocked\\n'; {holder_tail} ;;
        *pg_terminate_backend*) printf 't\\n' ;;
        *pg_stat_activity*) printf '1\\n' ;;
        *pg_advisory_unlock*) printf '1\\n1\\n' ;;
        *'SELECT wav_path'*) printf '/data/canary.wav\\n' ;;
    esac
    return 0
}}
CANARY_PID=$$
run_diarization_canary {VIDEO_ID}
"""
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "LOCK_MODE": lock,
        "CANARY_LOG": str(log),
    }
    result = subprocess.run(["bash", "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_canary_happy_path_reaches_worker_cleanup_evidence_and_lock_release(tmp_path: Path) -> None:
    result, calls = _run_canary(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "--profile diarization run --rm --no-deps --name" in calls
    assert "container ls --all --no-trunc" in calls
    assert "SELECT count(*)::text" in calls
    assert "diarization_state='completed'" in calls
    assert "container rm --force" not in calls  # no canary container existed
    assert re.search(r"lock_application_name=hasanara-diarization-[0-9a-f-]{36}", calls)


def test_canary_aborts_before_worker_when_lock_holder_dies(tmp_path: Path) -> None:
    result, calls = _run_canary(tmp_path, lock="dead")
    assert result.returncode != 0
    assert "--name hasanara-diarization-canary" not in calls
    assert "lock holder died" in result.stderr


def test_canary_requeue_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    result, _ = _run_canary(tmp_path, requeue_fails=True)
    assert result.returncode != 0


def test_signal_before_backend_pid_parsing_terminates_unique_holder_and_reacquires_lock(tmp_path: Path) -> None:
    result, calls = _run_canary(tmp_path, interrupt_before_pid=True)
    assert result.returncode != 0
    assert "application_name = :'lock_application_name'" in calls
    assert re.search(r"lock_application_name=hasanara-diarization-[0-9a-f-]{36}", calls)
    assert "pg_terminate_backend(pid)" in calls
    assert "pg_try_advisory_lock" in calls
    assert "pg_advisory_unlock" in calls


@pytest.mark.parametrize("mode", ["daemon_fail", "present_stop_fail", "present_rm_fail"])
def test_cleanup_failures_are_not_reported_as_success(tmp_path: Path, mode: str) -> None:
    result, calls = _run_canary(tmp_path, docker_mode=mode)
    assert result.returncode != 0
    assert "container ls" in calls


def test_cleanup_absent_container_requires_a_working_daemon(tmp_path: Path) -> None:
    result, calls = _run_canary(tmp_path, docker_mode="absent")
    assert result.returncode == 0, result.stderr
    assert "container ls --all --no-trunc" in calls


def test_cleanup_rejects_replacement_race_before_stop_or_remove(tmp_path: Path) -> None:
    result, calls = _run_canary(tmp_path, docker_mode="replacement")
    assert result.returncode != 0
    assert "container inspect" in calls
    assert "container stop" not in calls
    assert "container rm" not in calls


def _run_emergency_cleanup(tmp_path: Path, *, lock: str = "healthy") -> tuple[subprocess.CompletedProcess[str], str]:
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> {log!s}
if [[ $* == *'container ls'* ]]; then exit 0; fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    code = f"""
source {SCRIPT!s}
compose() {{
    printf 'compose %s\\n' "$*" >> "$CANARY_LOG"
    case " $* " in
        *pg_sleep*) [[ ${{LOCK_MODE:-healthy}} == unavailable ]] && return 1; printf '4321\\n1\\nlocked\\n'; [[ ${{LOCK_MODE:-healthy}} == healthy ]] && exec sleep 8; return 0 ;;
        *pg_terminate_backend*) printf 't\\n' ;;
        *pg_stat_activity*) printf '1\\n' ;;
        *pg_advisory_unlock*) printf '1\\n1\\n' ;;
    esac
    return 0
}}
emergency_diarization_canary_cleanup {VIDEO_ID}
"""
    result = subprocess.run(
        ["bash", "-c", code],
        cwd=ROOT,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", ""),
            "LOCK_MODE": lock,
            "CANARY_LOG": str(log),
        },
        text=True,
        capture_output=True,
        timeout=15,
    )
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_emergency_cleanup_holds_lock_across_cleanup_and_running_row_requeue(tmp_path: Path) -> None:
    result, calls = _run_emergency_cleanup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls.index("pg_try_advisory_lock") < calls.index("container ls") < calls.index("UPDATE videos SET")
    assert "WHERE id=:'video_id'::uuid AND diarization_state='running'" in calls


def test_emergency_cleanup_fails_when_lock_is_unavailable(tmp_path: Path) -> None:
    result, calls = _run_emergency_cleanup(tmp_path, lock="unavailable")
    assert result.returncode != 0
    assert "container ls" not in calls
    assert "UPDATE videos SET" not in calls


def test_role_sql_contract_preserves_effective_column_grants_and_role_checks() -> None:
    sql = (ROOT / "scripts" / "check_diarization_role.sql").read_text(encoding="utf-8")
    for required in (
        "rolcanlogin",
        "NOT rolsuper",
        "NOT rolcreaterole",
        "NOT rolcreatedb",
        "NOT rolreplication",
        "NOT rolbypassrls",
        "effective_target_grants",
        "has_column_privilege",
        "required_grants EXCEPT SELECT * FROM effective_target_grants",
        "effective_target_grants EXCEPT SELECT * FROM required_grants",
        "has_sequence_privilege",
        "has_table_privilege",
        "non_system_routines",
        "non_system_types",
        "'DELETE'",
        "'TRUNCATE'",
        "'TRIGGER'",
    ):
        assert required in sql


def test_compose_helper_is_sourceable_and_feeds_role_sql_on_stdin() -> None:
    result = subprocess.run(
        ["bash", "-c", f"source {SCRIPT!s}; declare -F run_diarization_canary check_diarization_role"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    helper = SCRIPT.read_text(encoding="utf-8")
    assert "< scripts/check_diarization_role.sql" in helper


def test_lock_lifecycle_is_server_verified_and_traps_precede_acquisition_wait() -> None:
    helper = SCRIPT.read_text(encoding="utf-8")
    assert "generate_lock_application_name()" in helper
    assert "hasanara-diarization-$invocation_uuid" in helper
    assert "lock_application_name_is_valid" in helper
    assert "SELECT pg_backend_pid()" in helper
    assert "pg_stat_activity a JOIN pg_locks l" in helper
    assert "pg_terminate_backend" in helper
    assert "pg_advisory_unlock" in helper
    normal = helper.split("run_diarization_canary() {", 1)[1].split("\n}\n\n[[ ${BASH_SOURCE[0]}", 1)[0]
    assert normal.index("trap on_exit") < normal.index("start_lock_holder") < normal.index("await_lock_holder")
    emergency = helper.split("emergency_diarization_canary_cleanup() {", 1)[1].split(
        "\n}\n\nrun_diarization_canary", 1
    )[0]
    assert (
        emergency.index("trap on_emergency_exit")
        < emergency.index("start_lock_holder")
        < emergency.index("await_lock_holder")
    )
