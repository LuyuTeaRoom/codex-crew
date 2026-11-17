"""Pure routing and handoff checks for codex-crew 0.5.0.

This is deliberately a small, pure policy model.  It does not invoke Codex or
persist runtime state; production callers must still perform the documented
real canary on the exact dispatch path.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


GPT_ALLOWLIST = frozenset(
    {
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6-luna",
    }
)
AVAILABILITY_CLASSES = frozenset(
    {
        "QUOTA_EXHAUSTED",
        "RATE_LIMITED",
        "MODEL_ACCESS_DENIED",
        "MODEL_PARAMETER_INCOMPATIBLE",
        "MODEL_OVERRIDE_NOT_EXPOSED",
        "AUTH_FAILURE",
        "NETWORK_FAILURE",
        "TASK_FAILURE",
    }
)
# Ordered alternatives, not a mandatory multi-model pipeline. Astra entries
# are replaced by coordinator_model; never spawn a fixed-effort coordinator.
TASK_ROUTES = {
    "deterministic": (),  # use a program; no model dispatch
    "bounded": ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"),
    "small": ("gpt-6-astra",),
    "medium": ("gpt-6.1-sol", "gpt-6-astra"),
    "batch": ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"),
    "verification": ("gpt-6.1-sol", "gpt-6-astra"),
    "high_risk": ("gpt-6-astra",),
}
LONG_HORIZON_MODELS = {
    "scope": "gpt-6-astra",
    "bulk": "gpt-6-luna",
    "implementation": "gpt-6.1-sol",
    "verification": "gpt-6.1-sol",
    "decision": "gpt-6-astra",
}
HANDOFF_PACKET_FIELDS = frozenset(
    {"goal", "verified_facts", "artifact_index", "decisions", "open_items", "validation"}
)
BRIEF_FIELDS = frozenset({"task_id", "revision", "scope", "do_not_touch", "expected_result", "acceptance", "context"})
DEFAULT_MAX_CHARS = 12_000


def crew_start_allowed(*, model: str | None, effort: str | None, source: str | None) -> bool:
    """Evaluate current caller-supplied evidence; does not read UI or enforce dispatch.

    source must describe current runtime evidence or an explicit user statement
    about this conversation, never a historical/default configuration.
    """
    return model in ("gpt-6-astra", "gpt-6.1-sol") and source in (
        "current_runtime", "current_rollout", "current_user_statement"
    )


def remaining_percent(used_percent: float | int | None) -> float | None:
    """Return remaining capacity; null/absent data remains unknown."""
    if used_percent is None:
        return None
    return max(0, min(100, 100 - used_percent))


def endpoint_preflight_status(*, max_output_tokens_exposed: bool, requested_max_output_tokens: int | None, response_created: bool | None) -> str:
    """Classify endpoint preflight applicability without weakening worker canaries."""
    if not max_output_tokens_exposed:
        return "NOT_APPLICABLE"
    if requested_max_output_tokens != 1:
        return "FAIL"
    return "PASS" if response_created else "FAIL"


def canary_reusable(evidence: CanaryEvidence, *, task_id: str, model: str, call_path: str) -> bool:
    """Reuse successful reachability evidence only for this task and exact path."""
    return bool(task_id and model and call_path) and (
        evidence.task_id == task_id and evidence.model == model
        and evidence.call_path == call_path and evidence.result == "MODEL_REACHABLE"
        and evidence.raw_error_code is None and evidence.ttl == "current task"
    )


def classify_failure(*, error_code: str | None, message: str | None) -> str:
    """Classify a reported failure, not a wait timeout or missing status.

    Call only after execution failure is established; generic 429 is not quota.
    """
    source = f"{error_code or ''} {message or ''}".lower()
    if "reasoning.summary" in source and ("unsupported" in source or "unknown" in source):
        return "MODEL_PARAMETER_INCOMPATIBLE"
    if "unknown model" in source and "spawn_agent" in source:
        return "MODEL_OVERRIDE_NOT_EXPOSED"
    if "insufficient_quota" in source or "billing quota" in source:
        return "QUOTA_EXHAUSTED"
    if "429" in source or "rate limit" in source:
        return "RATE_LIMITED"
    if "access denied" in source or "model_not_found" in source:
        return "MODEL_ACCESS_DENIED"
    if "auth" in source or "unauthorized" in source or "401" in source:
        return "AUTH_FAILURE"
    if "network" in source or "timeout" in source or "connection" in source:
        return "NETWORK_FAILURE"
    return "TASK_FAILURE"


def quota_exhausted(*, used_percent: float | int | None, resets_at: int | None = None, error_code: str | None, same_model_canary: str | None) -> bool:
    """Apply the two allowed quota-exhaustion evidence paths."""
    # A future reset timestamp describes when the window resets; it does not
    # change a current usedPercent=100 conclusion.
    _ = resets_at
    if used_percent is not None and used_percent >= 100:
        return True
    return classify_failure(error_code=error_code, message=None) == "QUOTA_EXHAUSTED" and same_model_canary == "QUOTA_EXHAUSTED"


def clear_unavailable_cache(*, user_reported_upgrade_or_reset: bool, cache: dict[str, object]) -> dict[str, object]:
    """Return a fresh task cache when a user reports a plan change/reset."""
    return {} if user_reported_upgrade_or_reset else dict(cache)


def external_model_allowed(*, model: str, current_task_explicit_models: Iterable[str] = (), current_task_explicit_providers: Iterable[str] = ()) -> bool:
    """External authorization must be explicit and limited to the current task."""
    if model in GPT_ALLOWLIST:
        return True
    provider = model.split("/", 1)[0] if "/" in model else ""
    return model in set(current_task_explicit_models) or provider in set(current_task_explicit_providers)


def dispatch_candidates(*, task_kind: str, available_models: Iterable[str], coordinator_model: str = "gpt-6-astra", current_task_explicit_models: Iterable[str] = (), current_task_explicit_providers: Iterable[str] = ()) -> tuple[str, ...]:
    """Filter alternatives using caller-verified availability and authorization.

    This helper does not probe models, enforce the startup gate, or decide when to retry or escalate.
    Caller must check crew_start_allowed before delegation. deterministic means use a program;
    bounded applies to investigation, implementation or review, not a fixed role.
    Pass the verified coordinator_model. Its entry means inline work at the user's
    effort, not a worker spawn. Independent review still needs a separate context.
    """
    if task_kind not in TASK_ROUTES:
        raise ValueError(f"unknown task kind: {task_kind}")
    if coordinator_model not in ("gpt-6-astra", "gpt-6.1-sol"):
        raise ValueError(f"unsupported coordinator: {coordinator_model}")
    routes = dict.fromkeys(coordinator_model if model == "gpt-6-astra" else model
                           for model in TASK_ROUTES[task_kind])
    available = set(available_models)
    return tuple(
        model
        for model in routes
        if model in available
        and external_model_allowed(
            model=model,
            current_task_explicit_models=current_task_explicit_models,
            current_task_explicit_providers=current_task_explicit_providers,
        )
    )


def long_horizon_model(stage: str, *, coordinator_model: str = "gpt-6-astra") -> str:
    """Legacy stage default for compatibility; use capability routes for new assignments."""
    if coordinator_model not in ("gpt-6-astra", "gpt-6.1-sol"):
        raise ValueError(f"unsupported coordinator: {coordinator_model}")
    try:
        model = LONG_HORIZON_MODELS[stage]
        return coordinator_model if model == "gpt-6-astra" else model
    except KeyError as exc:
        raise ValueError(f"unknown long-horizon stage: {stage}") from exc


def file_version(path: Path) -> str:
    """Version only an explicitly referenced artifact, including uncommitted bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object, *, nonempty: bool = False) -> bool:
    return isinstance(value, list) and (not nonempty or bool(value)) and all(_text(item) for item in value)


def validate_brief(brief: object, *, base_dir: Path, max_chars: int = DEFAULT_MAX_CHARS) -> list[dict[str, str]]:
    """Validate the dispatched JSON brief, not factual truth or task completion.

    Artifact paths are local to base_dir. External evidence is a saved excerpt
    with its origin recorded in the artifact's optional source_url field.
    """
    errors: list[dict[str, str]] = []

    def error(field: str, message: str) -> None:
        errors.append({"field": field, "message": message})

    if type(max_chars) is not int or max_chars <= 0:
        return [{"field": "max_chars", "message": "must be a positive integer"}]
    try:
        size = len(json.dumps(brief, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        return [{"field": "$", "message": "must be JSON data"}]
    if size > max_chars:
        return [{"field": "$", "message": f"{size} characters exceeds {max_chars}; narrow scope or explicitly raise the limit"}]
    if not isinstance(brief, dict) or set(brief) != BRIEF_FIELDS:
        return [{"field": "$", "message": "expected exactly: " + ", ".join(sorted(BRIEF_FIELDS))}]
    for key in ("task_id", "expected_result"):
        if not _text(brief[key]):
            error(key, "must be nonempty text")
    if type(brief["revision"]) is not int or brief["revision"] < 1:
        error("revision", "must be a positive integer")
    for key in ("scope", "do_not_touch"):
        if not _strings(brief[key], nonempty=key == "scope"):
            error(key, "must be a list of nonempty strings; scope must not be empty")
    acceptance = brief["acceptance"]
    if not isinstance(acceptance, dict) or not acceptance or not all(_text(k) and _text(v) for k, v in acceptance.items()):
        error("acceptance", "must map criterion IDs to nonempty requirements")
        acceptance = {}
    packet = brief["context"]
    if not isinstance(packet, dict) or set(packet) != HANDOFF_PACKET_FIELDS:
        error("context", "expected exactly the six Context Packet fields")
        return errors
    if not _text(packet["goal"]):
        error("context.goal", "must be nonempty text")
    for key in HANDOFF_PACKET_FIELDS - {"goal"}:
        if not isinstance(packet[key], list):
            error("context." + key, "must be a list; use [] when nothing is known")
    if any(not isinstance(packet[key], list) for key in HANDOFF_PACKET_FIELDS - {"goal"}):
        return errors
    for key in ("decisions", "open_items"):
        if not _strings(packet[key]):
            error("context." + key, "must contain nonempty strings")

    artifacts: dict[str, dict] = {}
    for i, item in enumerate(packet["artifact_index"]):
        field = f"context.artifact_index[{i}]"
        if not isinstance(item, dict) or not all(_text(item.get(k)) for k in ("id", "path", "range", "version")):
            error(field, "requires id, local path, range and version")
            continue
        if item["id"] in artifacts:
            error(field + ".id", "duplicate artifact ID")
        artifacts[item["id"]] = item
        path = base_dir / item["path"]
        try:
            if not path.is_file():
                error(field + ".path", "evidence file does not exist; planned outputs belong in expected_result")
            elif file_version(path) != item["version"]:
                error(field + ".version", "does not match current file contents")
        except (OSError, ValueError):
            error(field + ".path", "evidence file cannot be read")

    def references(value: object, field: str) -> None:
        if not _strings(value, nonempty=True):
            error(field, "must contain at least one artifact ID")
        else:
            for ref in value:
                if ref not in artifacts:
                    error(field, f"unknown artifact ID: {ref}")

    for i, fact in enumerate(packet["verified_facts"]):
        field = f"context.verified_facts[{i}]"
        if not isinstance(fact, dict) or not _text(fact.get("text")):
            error(field, "requires nonempty text and sources")
        else:
            references(fact.get("sources"), field + ".sources")

    seen_criteria: set[str] = set()
    for i, record in enumerate(packet["validation"]):
        field = f"context.validation[{i}]"
        if not isinstance(record, dict):
            error(field, "must be an object")
            continue
        criterion = record.get("criterion")
        if not _text(criterion) or criterion not in acceptance:
            error(field + ".criterion", "must reference a current acceptance criterion")
        elif criterion in seen_criteria:
            error(field + ".criterion", "duplicate current verdict; index older logs as evidence")
        else:
            seen_criteria.add(criterion)
        status = record.get("status")
        if status in ("PASS", "FAIL"):
            if not _text(record.get("method")):
                error(field + ".method", "executed checks require a method")
            references(record.get("evidence"), field + ".evidence")
            references(record.get("inputs"), field + ".inputs")
            inputs = record.get("inputs")
            if _strings(inputs, nonempty=True) and all(ref in artifacts for ref in inputs):
                versions = {ref: artifacts[ref]["version"] for ref in inputs}
                if record.get("input_versions") != versions:
                    error(field + ".input_versions", "must record the versions used when the check ran")
            if not result_is_current(record, task_id=brief["task_id"], revision=brief["revision"]):
                error(field, "executed check belongs to a different task or requirement revision")
        elif status in ("NOT_RUN", "BLOCKED"):
            if not _text(record.get("reason")):
                error(field + ".reason", "unexecuted checks require a reason")
        else:
            error(field + ".status", "expected PASS, FAIL, NOT_RUN or BLOCKED")
    return errors


RESULT_FIELDS = frozenset({"task_id", "revision", "brief_version", "status", "summary",
                           "actual_changes", "artifact_index", "validation", "unfinished"})


def brief_version(brief: dict) -> str:
    """Fingerprint the dispatched JSON content, independent of whitespace/key order."""
    raw = json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_result(result: object, *, brief: dict, base_dir: Path,
                    max_chars: int = DEFAULT_MAX_CHARS) -> list[dict[str, str]]:
    """Check a return against the parent's dispatched brief and current evidence.

    The original brief must have passed dispatch validation. Do not recheck its
    pre-edit file hashes: the worker may legitimately have changed those files.
    The returned artifact index describes current outputs. No model calls, file
    writes, test execution, or semantic certification happen here.
    """
    def issue(field: str, message: str) -> dict[str, str]:
        return {"field": field, "message": message}

    if type(max_chars) is not int or max_chars <= 0:
        return [issue("max_chars", "must be a positive integer")]
    if not isinstance(result, dict) or set(result) != RESULT_FIELDS:
        return [issue("$", "expected exactly: " + ", ".join(sorted(RESULT_FIELDS)))]
    if not isinstance(brief, dict) or set(brief) != BRIEF_FIELDS or not isinstance(brief.get("context"), dict):
        return [issue("brief", "requires the original validated dispatch brief")]
    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        version = brief_version(brief)
    except (TypeError, ValueError, RecursionError):
        return [issue("$", "must be JSON data")]
    if size > max_chars:
        return [issue("$", f"{size} characters exceeds {max_chars}; use evidence references")]
    errors = []
    if not result_is_current(result, task_id=brief.get("task_id"), revision=brief.get("revision")):
        errors.append(issue("revision", "result belongs to a different task or requirement revision; inspect existing writes"))
    if result["brief_version"] != version:
        errors.append(issue("brief_version", "does not match the parent's dispatched brief; do not replace its constraints or acceptance"))
    if result["status"] not in ("PASS", "PARTIAL", "FAIL", "BLOCKED"):
        errors.append(issue("status", "expected PASS, PARTIAL, FAIL or BLOCKED"))
    if not _text(result["summary"]):
        errors.append(issue("summary", "must be nonempty text"))
    for key in ("actual_changes", "unfinished"):
        if not _strings(result[key]):
            errors.append(issue(key, "must be a list of nonempty strings; use [] when empty"))
    if not isinstance(result["validation"], list) or not isinstance(result["artifact_index"], list):
        return errors + [issue("$", "validation and artifact_index must be lists")]

    # Reuse the existing evidence format and validator, with authoritative
    # acceptance/scope/constraints from the parent and fresh returned evidence.
    packet = {**brief, "context": {
        "goal": brief["context"].get("goal"), "verified_facts": [],
        "artifact_index": result["artifact_index"], "validation": result["validation"],
        "decisions": [], "open_items": [],
    }}
    combined_size = len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")))
    errors.extend(validate_brief(packet, base_dir=base_dir, max_chars=max(max_chars, combined_size)))
    acceptance = brief.get("acceptance")
    if not isinstance(acceptance, dict):
        return errors
    covered = {r.get("criterion") for r in result["validation"]
               if isinstance(r, dict) and isinstance(r.get("criterion"), str)}
    missing = set(acceptance) - covered
    if missing:
        errors.append(issue("validation", "missing acceptance criteria: " + ", ".join(sorted(missing))))
    for i, record in enumerate(result["validation"]):
        if isinstance(record, dict) and record.get("status") in ("PASS", "FAIL"):
            if not isinstance(record.get("conditions"), dict) or not record["conditions"]:
                errors.append(issue(f"validation[{i}].conditions", "executed checks require their relevant conditions"))
    if result["status"] == "PASS":
        if result["unfinished"] or any(not isinstance(r, dict) or r.get("status") != "PASS"
                                      for r in result["validation"]):
            errors.append(issue("status", "PASS requires every acceptance criterion to pass and no unfinished work"))
    elif not result["unfinished"]:
        errors.append(issue("unfinished", "non-PASS results must explain what remains or blocks completion"))
    return errors


def result_is_current(result: dict, *, task_id: str, revision: int) -> bool:
    """Reject stale summaries; this does not cancel or undo their file writes."""
    return bool(task_id) and type(revision) is int and revision > 0 and (
        result.get("task_id") == task_id and type(result.get("revision")) is int
        and result["revision"] == revision
    )


def validation_reusable(record: dict, *, task_id: str, revision: int, input_versions: dict[str, str], method: str, conditions: dict, dependencies_known: bool) -> bool:
    """Reuse only after the caller checks relevant inputs and execution conditions."""
    return dependencies_known is True and result_is_current(record, task_id=task_id, revision=revision) and (
        record.get("status") == "PASS" and _strings(record.get("evidence"), nonempty=True)
        and bool(input_versions) and all(_text(v) for v in input_versions.values())
        and record.get("input_versions") == input_versions
        and _text(method) and record.get("method") == method
        and isinstance(conditions, dict) and record.get("conditions") == conditions
    )


def may_parallelize(*, independent: bool, overlapping_writes: bool) -> bool:
    """Parallel work needs independent scope and no shared mutable target."""
    return independent and not overlapping_writes


def finite_state_legal(applicability: str, status: str) -> bool:
    """Reference 2x4 state space used by the verification guidance."""
    return (applicability == "APPLIES" and status in {"PASS", "FAIL", "BLOCKED"}) or (
        applicability == "NOT_APPLICABLE" and status == "NOT_APPLICABLE"
    )


@dataclass(frozen=True)
class CanaryEvidence:
    task_id: str
    utc: str
    model: str
    call_path: str
    raw_error_code: str | None
    raw_fields: dict[str, object]
    result: str
    ttl: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a crew brief or returned result; no model calls or deployment.")
    parser.add_argument("brief", type=Path)
    parser.add_argument("--result", type=Path, help="check a returned JSON result against this original dispatch brief")
    parser.add_argument("--base-dir", type=Path, default=Path.cwd(), help="workspace for relative artifact paths (default: cwd)")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args()
    version = None
    try:
        raw = args.brief.read_text(encoding="utf-8")
        if len(raw) > args.max_chars:
            errors = [{"field": "$", "message": f"raw JSON is {len(raw)} characters; limit is {args.max_chars}"}]
        else:
            brief = json.loads(raw, object_pairs_hook=_unique_object)
            if args.result:
                returned = args.result.read_text(encoding="utf-8")
                if len(returned) > args.max_chars:
                    errors = [{"field": "$", "message": "raw result JSON exceeds the size limit"}]
                else:
                    result = json.loads(returned, object_pairs_hook=_unique_object)
                    errors = validate_result(result, brief=brief, base_dir=args.base_dir, max_chars=args.max_chars)
            else:
                errors = validate_brief(brief, base_dir=args.base_dir, max_chars=args.max_chars)
            if not errors:
                version = brief_version(brief)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        errors = [{"field": "$", "message": str(exc)}]
    print(json.dumps({"result_valid" if args.result else "packet_valid": not errors,
                      "brief_version": version, "errors": errors, "task_completion_verified": False}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
