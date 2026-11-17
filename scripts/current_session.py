"""Read current-turn evidence without changing Codex settings or loading history."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

from policy_contract import crew_start_allowed


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)


def read_primary_identity(home: Path, thread_id: str | None) -> bool | None:
    """Thread identity is independent of model/effort and cannot be overridden by them."""
    if not thread_id:
        return None
    with _connect(home / "state_5.sqlite") as db:
        row = db.execute("SELECT source FROM threads WHERE id=?", (thread_id,)).fetchone()
    return None if row is None or not row[0] else "subagent" not in str(row[0])


def read_current_turn(home: Path, thread_id: str | None, turn_id: str | None = None) -> dict:
    """Require an exact thread, a single active turn, and its indexed byte offset.

    Do not search other threads, choose the newest global record, or use defaults.
    Legacy logs without an active-turn offset remain unknown.
    """
    if not thread_id:
        raise ValueError("current thread ID unavailable")
    with _connect(home / "state_5.sqlite") as db:
        row = db.execute("SELECT rollout_path, source FROM threads WHERE id=?", (thread_id,)).fetchone()
    if row is None:
        raise ValueError("current thread not indexed")
    path, origin = Path(row[0]), row[1]
    with _connect(home / "thread_history_1.sqlite") as db:
        active = db.execute(
            "SELECT turn_id, rollout_byte_offset FROM thread_turns WHERE thread_id=? AND status='inProgress'",
            (thread_id,),
        ).fetchall()
    if len(active) != 1 or (turn_id is not None and active[0][0] != turn_id):
        raise ValueError("current active turn is missing, ambiguous, or mismatched")
    actual_turn, offset = active[0]
    if type(offset) is not int or offset < 0:
        raise ValueError("active turn byte offset unavailable")
    with path.open("rb") as stream:
        first = json.loads(stream.readline())
        if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != thread_id:
            raise ValueError("rollout identity mismatch")
        stream.seek(offset)
        # Read only the indexed turn prefix. Never replay the complete conversation.
        budget = 8 * 1024 * 1024
        for _ in range(128):
            position = stream.tell()
            line = stream.readline(budget + 1)
            budget -= len(line)
            if not line or budget < 0:
                break
            event = json.loads(line)
            payload = event.get("payload", {})
            if event.get("type") == "turn_context" and payload.get("turn_id") == actual_turn:
                # Recheck status after the file read, rather than accepting a completed turn.
                with _connect(home / "thread_history_1.sqlite") as db:
                    current = db.execute(
                        "SELECT turn_id FROM thread_turns WHERE thread_id=? AND status='inProgress'",
                        (thread_id,),
                    ).fetchall()
                if current != [(actual_turn,)]:
                    raise ValueError("active turn changed during read")
                return {
                    "model": payload.get("model"),
                    "effort": payload.get("effort") or payload.get("reasoning_effort"),
                    "source": "current_rollout",
                    "thread_id": thread_id,
                    "turn_id": actual_turn,
                    "primary": "subagent" not in str(origin),
                    "evidence": {"path": str(path), "byte_offset": position},
                }
    raise ValueError("matching current-turn context not available in indexed prefix")


def resolve_settings(*, runtime: dict | None = None, session_reader=None, user: dict | None = None, primary: bool | None = None) -> dict:
    """Use current runtime -> indexed active turn -> explicit current user statement.

    Known unsupported models or Worker identities stop resolution.
    Partial higher-priority evidence constrains fallback; sources are not merged.
    Values supplied as runtime/user evidence must already be verified by the caller.
    """
    if primary is False:
        return {"model": None, "effort": None, "source": "current_thread", "primary": False,
                "crew_start_allowed": False, "attempts": [{"source": "current_thread", "reason": "known Worker identity cannot start coordination"}]}
    attempts = []
    candidates = [("current_runtime", lambda: runtime)]
    if session_reader is not None:
        candidates.append(("current_rollout", session_reader))
    candidates.append(("current_user_statement", lambda: user))
    partial = {}
    for source, read in candidates:
        try:
            value = read()
        except (OSError, ValueError, sqlite3.Error) as exc:
            attempts.append({"source": source, "reason": str(exc)})
            continue
        if not isinstance(value, dict):
            attempts.append({"source": source, "reason": "current model evidence unavailable"})
            continue
        model, effort = value.get("model"), value.get("effort")
        # A partial model cannot silently become a different model at fallback.
        # Effort is not a startup gate; preserve higher-priority evidence.
        conflict = (model and partial.get("model") and model != partial["model"]
                    or effort and partial.get("effort")
                    and effort != partial["effort"])
        if conflict:
            attempts.append({"source": source, "reason": "conflicts with higher-priority current evidence"})
            break
        known_denial = (model is not None and model not in ("gpt-6-astra", "gpt-6.1-sol")
                        or value.get("primary") is False)
        complete = model in ("gpt-6-astra", "gpt-6.1-sol")
        if not known_denial and not complete:
            partial.update({k: value[k] for k in ("model", "effort") if value.get(k) is not None})
            attempts.append({"source": source, "reason": "current evidence incomplete for startup"})
            continue
        result = {**value, "source": source}
        result["crew_start_allowed"] = value.get("primary", True) and crew_start_allowed(
            model=value.get("model"), effort=value.get("effort"), source=source,
        )
        result["attempts"] = attempts
        return result
    return {"model": None, "effort": None, "source": "unknown", "crew_start_allowed": False, "attempts": attempts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--turn-id", default=os.environ.get("CODEX_TURN_ID"))
    parser.add_argument("--runtime-model")
    parser.add_argument("--runtime-effort")
    parser.add_argument("--user-model")
    parser.add_argument("--user-effort")
    args = parser.parse_args()
    try:
        primary = read_primary_identity(args.home, args.thread_id)
    except (OSError, sqlite3.Error):
        primary = None
    result = resolve_settings(
        primary=primary,
        runtime={"model": args.runtime_model, "effort": args.runtime_effort},
        session_reader=lambda: read_current_turn(args.home, args.thread_id, args.turn_id),
        user={"model": args.user_model, "effort": args.user_effort},
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["source"] != "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
