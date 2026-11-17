#!/usr/bin/env python3
"""Measure token usage already present in local Codex rollout JSONL files."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


FIELDS = (
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens", "total_tokens",
)
END_EVENTS = {
    "task_complete", "turn_complete", "turn_completed", "turn_aborted",
    "task_failed", "turn_failed", "task_cancelled", "turn_cancelled",
}
ROLES = {"coordinator", "worker"}


def valid_count(value):
    return value if type(value) is int and value >= 0 else None


def normalize_usage(data: dict) -> dict:
    result = {field: valid_count(data.get(field)) for field in FIELDS}
    if result["input_tokens"] is not None and result["cached_input_tokens"] is not None:
        if result["cached_input_tokens"] > result["input_tokens"]:
            raise ValueError("cached_input_tokens exceeds input_tokens")
    if result["output_tokens"] is not None and result["reasoning_output_tokens"] is not None:
        if result["reasoning_output_tokens"] > result["output_tokens"]:
            raise ValueError("reasoning_output_tokens exceeds output_tokens")
    return result


def add_usage(events: list[dict]) -> dict:
    result = {}
    for field in FIELDS:
        values = [event["usage"].get(field) for event in events]
        result[field] = sum(values) if values and all(value is not None for value in values) else None
    input_tokens, cached = result["input_tokens"], result["cached_input_tokens"]
    result["non_cached_input_tokens"] = input_tokens - cached if input_tokens is not None and cached is not None else None
    return result


def _payload(row: dict) -> dict:
    value = row.get("payload")
    return value if isinstance(value, dict) else {}


def _turn_id(row: dict) -> str | None:
    payload = _payload(row)
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    return row.get("turn_id") or payload.get("turn_id") or response.get("turn_id")


def _active_for_target(row: dict, target: str, active: bool) -> bool:
    row_turn = _turn_id(row)
    return row_turn == target if row_turn is not None else active


def _usage_candidates(row: dict) -> list[tuple[dict, str]]:
    payload = _payload(row)
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    candidates = []
    for owner in (row, payload, info, response):
        for key in ("usage", "last_token_usage"):
            value = owner.get(key)
            if isinstance(value, dict):
                candidates.append((value, key))
    return candidates


def _native_response_id(row: dict) -> str | None:
    payload = _payload(row)
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    for value in (
        row.get("response_id"), payload.get("response_id"),
        info.get("response_id"), response.get("response_id"), response.get("id"),
    ):
        if isinstance(value, str) and value:
            return value
    kind = payload.get("type")
    if kind in {"response", "response.completed", "response_completed"}:
        value = payload.get("id") or row.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _end_event(kind: str) -> bool:
    return kind in END_EVENTS


def _base_source(source: dict) -> dict:
    path = Path(source["path"]).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path),
        "thread_id": str(source["thread_id"]),
        "turn_id": str(source["turn_id"]),
        "role": source["role"],
    }


def extract_source(source: dict) -> dict:
    """Read one thread/turn, preferring native response usage over cumulative deltas."""
    base = _base_source(source)
    path = Path(base["path"])
    rows = []
    parse_errors = 0
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if isinstance(row, dict):
                rows.append((number, row))

    active = False
    started = False
    ended_at = None
    completion_kind = None
    native_records = {}
    native_usage_found = False
    cumulative_rows = []
    for number, row in rows:
        payload = _payload(row)
        kind = payload.get("type") if row.get("type") == "event_msg" else None
        turn = _turn_id(row)
        if kind == "task_started":
            active = turn == base["turn_id"]
            started = started or active
        matches = _active_for_target(row, base["turn_id"], active)
        if kind and _end_event(kind) and matches:
            ended_at = row.get("timestamp")
            completion_kind = kind
            active = False

        response_id = _native_response_id(row)
        candidates = _usage_candidates(row)
        response_usage = next((usage for usage, _ in candidates), None)
        if response_id and matches:
            started = True
            if response_usage is not None:
                native_usage_found = True
                previous = native_records.get(response_id)
                if previous is None or previous.get("usage_missing"):
                    native_records[response_id] = {
                        "response_id": response_id,
                        "timestamp": row.get("timestamp"),
                        "line": number,
                        "usage": normalize_usage(response_usage),
                    }
            elif response_id not in native_records:
                native_records[response_id] = {
                    "response_id": response_id,
                    "timestamp": row.get("timestamp"),
                    "line": number,
                    "usage": {field: None for field in FIELDS},
                    "usage_missing": True,
                }
        if kind == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            cumulative = info.get("total_token_usage")
            if isinstance(cumulative, dict):
                cumulative_rows.append((number, row, matches, normalize_usage(cumulative)))

    if native_usage_found:
        method = "native_response_usage"
        events = list(native_records.values())
    else:
        method = "cumulative_total_delta"
        events = []
        prior = None
        for number, row, matches, cumulative in cumulative_rows:
            if matches:
                delta = {}
                for field in FIELDS:
                    current = cumulative[field]
                    previous = prior.get(field) if prior is not None else 0
                    if current is None or previous is None:
                        delta[field] = None
                    elif current < previous:
                        raise ValueError(f"cumulative usage reset at {path}:{number}; cannot safely difference this source")
                    else:
                        delta[field] = current - previous
                events.append({
                    "timestamp": row.get("timestamp"),
                    "line": number,
                    "usage": delta,
                })
                started = True
            prior = cumulative

    turn_state = "complete" if ended_at else ("snapshot" if started else "not_found")
    return {
        **base,
        "turn_state": turn_state,
        "completion_kind": completion_kind,
        "ended_at": ended_at,
        "method": method,
        "events": events,
        "event_count": len(events),
        "parse_errors": parse_errors,
        "usage": add_usage(events),
    }


def load_sources(args: argparse.Namespace) -> list[dict]:
    if args.source_json:
        config_path = Path(args.source_json).expanduser().resolve()
        data = json.loads(config_path.read_text())
        raw_sources = data.get("sources") if isinstance(data, dict) else data
        if not isinstance(raw_sources, list):
            raise ValueError("source JSON must be a list or an object with a sources list")
        sources = []
        for entry in raw_sources:
            if not isinstance(entry, dict):
                raise ValueError("each source must be an object")
            resolved = dict(entry)
            source_path = Path(resolved["path"]).expanduser()
            if not source_path.is_absolute():
                resolved["path"] = str(config_path.parent / source_path)
            sources.append(resolved)
    else:
        if not all((args.rollout, args.thread_id, args.turn_id, args.role)):
            raise ValueError("provide --source-json or all of --rollout, --thread-id, --turn-id, and --role")
        sources = [{
            "path": args.rollout,
            "thread_id": args.thread_id,
            "turn_id": args.turn_id,
            "role": args.role,
        }]
    for source in sources:
        if source.get("role") not in ROLES:
            raise ValueError("source role must be coordinator or worker")
        if not all(source.get(key) for key in ("path", "thread_id", "turn_id")):
            raise ValueError("each source requires path, thread_id, turn_id, and role")
    return sources


def _sum_sources(sources: list[dict]) -> dict:
    result = {}
    for field in FIELDS:
        values = [source["usage"].get(field) for source in sources]
        result[field] = sum(values) if values and all(value is not None for value in values) else None
    input_tokens, cached = result["input_tokens"], result["cached_input_tokens"]
    result["non_cached_input_tokens"] = input_tokens - cached if input_tokens is not None and cached is not None else None
    return result


def build_report(raw_sources: list[dict]) -> dict:
    extracted = []
    seen_source_keys = {}
    for raw in raw_sources:
        base = _base_source(raw)
        key = (base["path"], base["thread_id"], base["turn_id"])
        prior_role = seen_source_keys.get(key)
        if prior_role:
            if prior_role != base["role"]:
                raise ValueError(f"duplicate source is assigned conflicting roles: {base['path']}")
            continue
        seen_source_keys[key] = base["role"]
        extracted.append(extract_source(base))

    seen_response_ids = {}
    cross_source_duplicates = []
    for source in extracted:
        if source["method"] != "native_response_usage":
            continue
        raw_events = source["events"]
        source["raw_event_count"] = len(raw_events)
        unique = []
        for event in raw_events:
            response_id = event["response_id"]
            if response_id in seen_response_ids:
                duplicate = {"response_id": response_id, "source": source["path"],
                             "first_source": seen_response_ids[response_id]}
                cross_source_duplicates.append(duplicate)
                continue
            seen_response_ids[response_id] = source["path"]
            unique.append(event)
        source["duplicate_response_ids"] = [
            item["response_id"] for item in cross_source_duplicates if item["source"] == source["path"]
        ]
        source["events"] = unique
        source["event_count"] = len(unique)
        if not unique and source["raw_event_count"]:
            # Every observed response ID was already counted in an earlier source.
            source["usage"] = {field: 0 for field in FIELDS}
            source["usage"]["non_cached_input_tokens"] = 0
        else:
            source["usage"] = add_usage(unique)

    roles = {}
    for role in sorted(ROLES):
        rows = [source for source in extracted if source["role"] == role]
        states = {source["turn_state"] for source in rows}
        state = "complete" if rows and states == {"complete"} else (
            "snapshot" if "snapshot" in states else ("not_found" if rows else "no_sources")
        )
        roles[role] = {"source_count": len(rows), "turn_state": state, "usage": _sum_sources(rows)}

    states = {source["turn_state"] for source in extracted}
    overall_state = "complete" if extracted and states == {"complete"} else (
        "snapshot" if "snapshot" in states else ("not_found" if extracted else "no_sources")
    )
    cumulative_sources = [source for source in extracted if source["method"] == "cumulative_total_delta"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "turn_state": overall_state,
        "accounting": {
            "cached_input_tokens": "subset of input_tokens",
            "reasoning_output_tokens": "subset of output_tokens",
            "total_tokens": "reported total; not added to input or output",
            "non_cached_input_tokens": "input_tokens minus cached_input_tokens when both are known",
            "per_source_method": "Each source uses native response usage if available; otherwise cumulative total deltas. The two methods are never added for the same source.",
            "cumulative_source_note": "No response id is fabricated. Cumulative sources have no cross-source response-id deduplication." if cumulative_sources else None,
        },
        "sources": extracted,
        "roles": roles,
        "combined_usage": _sum_sources(extracted),
        "deduplication": {
            "native_response_id_duplicates": cross_source_duplicates,
            "cumulative_sources_without_response_ids": len(cumulative_sources),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", help="JSON list of rollout sources")
    parser.add_argument("--rollout", help="single local rollout JSONL path")
    parser.add_argument("--thread-id")
    parser.add_argument("--turn-id")
    parser.add_argument("--role", choices=sorted(ROLES))
    parser.add_argument("--output", required=True, help="report JSON output path")
    args = parser.parse_args()
    try:
        report = build_report(load_sources(args))
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"output": str(output), "turn_state": report["turn_state"], "combined_usage": report["combined_usage"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
