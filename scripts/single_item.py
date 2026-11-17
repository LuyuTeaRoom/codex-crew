#!/usr/bin/env python3
"""Start one explicitly approved command or inspect its existing managed job."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def manager_path(value: str | None) -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    path = Path(value or os.environ.get(
        "CODEX_PROCESS_MANAGER",
        codex_home / "process_manager" / "task_process.py",
    )).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"process manager not found: {path}")
    return path


def read_index(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "jobs": {}}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("jobs"), dict):
        raise ValueError(f"unsupported job index: {path}")
    return data


def update_index(path: Path, job_id: str, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_index(path)
        data["jobs"][job_id] = record
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)


def manager_call(manager: Path, *args: str) -> dict:
    run = subprocess.run(
        [sys.executable, str(manager), *args],
        capture_output=True, text=True, timeout=15,
    )
    if run.returncode:
        detail = run.stderr.strip() or run.stdout.strip() or f"exit {run.returncode}"
        raise RuntimeError(f"process manager {args[0]} failed: {detail}")
    try:
        value = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"process manager returned non-JSON output: {run.stdout[:500]}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("process manager returned a non-object result")
    return value


def result_summary(path: Path) -> dict:
    if not path.is_file():
        return {"state": "missing", "path": str(path)}
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"state": "invalid_json", "path": str(path), "detail": str(exc)}
    if isinstance(value, dict):
        summary = {key: value[key] for key in ("status", "summary", "task_id", "revision", "brief_version") if key in value}
        summary["keys"] = list(value)[:32]
    else:
        summary = {"json_type": type(value).__name__}
    return {"state": "available", "path": str(path), "summary": summary}


def snapshot(manager: Path, index: Path, job_id: str) -> dict:
    record = read_index(index)["jobs"].get(job_id)
    if record is None:
        raise ValueError(f"job id is not present in this index: {job_id}")
    job = manager_call(manager, "status", job_id)
    metadata_path = Path(job["metadata_path"])
    metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {}
    log_path = Path(job["log_path"])
    log_bytes = log_path.stat().st_size if log_path.is_file() else 0
    current = {
        "id": job.get("id", job_id),
        "task": job.get("task"),
        "turn": job.get("turn"),
        "name": job.get("name"),
        "status": job.get("status"),
        "pid": job.get("pid"),
        "exit_code": job.get("exit_code"),
        "group_remaining": metadata.get("group_remaining"),
        "started_at": metadata.get("started_at"),
        "ended_at": metadata.get("ended_at"),
        "timeout_s": job.get("timeout_s"),
        "error": job.get("error"),
        "log_path": str(log_path),
    }
    record.update(current)
    update_index(index, job_id, record)
    status = current["status"]
    assessment = "RUNNING" if status not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "OWNER_EXITED", "START_FAILED"} else (
        "PROCESS_SUCCEEDED" if status == "SUCCEEDED" and current["exit_code"] == 0 else "PROCESS_FAILED"
    )
    return {
        "job_id": job_id,
        "status": status,
        "assessment": assessment,
        "exit_code": current["exit_code"],
        "job": current,
        "log_index": {"path": str(log_path), "bytes": log_bytes},
        "job_index": str(index),
        "result": result_summary(Path(record["result_path"])),
    }


def start(args: argparse.Namespace) -> dict:
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be a finite positive number")
    if not math.isfinite(args.wait) or args.wait < 0:
        raise ValueError("--wait must be a finite non-negative number")
    current_task = os.environ.get("CODEX_THREAD_ID")
    if current_task and current_task != args.task:
        raise ValueError("--task must match CODEX_THREAD_ID for this managed job")
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise ValueError("a command after -- is required")
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise NotADirectoryError(cwd)
    index = Path(args.job_index).expanduser().resolve()
    manager = manager_path(args.manager)
    started = manager_call(
        manager, "start", "--task", args.task, "--turn", args.turn,
        "--name", args.name, "--timeout", str(args.timeout), "--cwd", str(cwd),
        "--", *command,
    )
    job_id = started.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("process manager did not return a job id")
    record = {
        "id": job_id,
        "task": args.task,
        "turn": args.turn,
        "name": args.name,
        "cwd": str(cwd),
        "command": command,
        "timeout_s": args.timeout,
        "result_path": str(Path(args.result).expanduser().resolve()),
        "created_at": started.get("created_at"),
        "log_path": started.get("log_path"),
        "metadata_path": started.get("metadata_path"),
    }
    update_index(index, job_id, record)
    deadline = time.monotonic() + args.wait
    while True:
        current = snapshot(manager, index, job_id)
        if current["status"] != "RUNNING" or time.monotonic() >= deadline:
            return current
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="launch exactly one explicit command")
    run.add_argument("--cwd", required=True)
    run.add_argument("--task", required=True, help="process manager chat/thread id")
    run.add_argument("--turn", required=True)
    run.add_argument("--name", required=True, help="human-readable managed job name")
    run.add_argument("--timeout", required=True, type=float, help="maximum process runtime in seconds")
    run.add_argument("--wait", type=float, default=0, help="maximum time to wait before returning a snapshot")
    run.add_argument("--result", required=True, help="expected JSON result path")
    run.add_argument("--job-index", required=True)
    run.add_argument("--manager", help="task_process.py path; defaults to CODEX_PROCESS_MANAGER or ~/.codex/process_manager/task_process.py")
    run.add_argument("command", nargs=argparse.REMAINDER)
    status = sub.add_parser("status", help="query an existing job without starting another")
    status.add_argument("--job-id", required=True)
    status.add_argument("--job-index", required=True)
    status.add_argument("--manager")
    args = parser.parse_args()
    try:
        manager = manager_path(args.manager)
        if args.action == "run":
            result = start(args)
        else:
            result = snapshot(manager, Path(args.job_index).expanduser().resolve(), args.job_id)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["assessment"] == "PROCESS_FAILED" else 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
