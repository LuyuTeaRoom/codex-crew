#!/usr/bin/env python3
"""Validate the shipped configuration, links and executable brief example."""
from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from policy_contract import GPT_ALLOWLIST, TASK_ROUTES, validate_brief

ROOT = Path(__file__).resolve().parent.parent
ROLE_CONFIGS = {
    "agent-luna.toml": ("codex_luna_max_worker", "gpt-6-luna", "max"),
    "agent-sol.toml": ("codex_sol_xhigh_worker", "gpt-6.1-sol", "xhigh"),
    "agent-spark.toml": ("codex_spark_worker", "gpt-5.3-codex-spark", "xhigh"),
}


def project_config_errors(data: dict) -> list[str]:
    errors = []
    if set(data) != {"agents"}:
        errors.append("project template must contain only agents options, without parent or permission overrides")
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return errors + ["agents must be a table"]
    if set(agents) != {"enabled", "max_concurrent_threads_per_session"}:
        errors.append("unexpected agents settings")
    if agents.get("enabled") is not True:
        errors.append("agents.enabled must be true")
    cap = agents.get("max_concurrent_threads_per_session")
    if type(cap) is not int or cap <= 0:
        errors.append("concurrency cap must be a positive integer")
    return errors


def role_config_errors(data: dict, *, name: str, model: str, effort: str) -> list[str]:
    """Check this package's templates, not arbitrary user Agent configurations."""
    errors = []
    allowed = {"name", "model", "model_reasoning_effort", "description", "developer_instructions"}
    if name == "codex_spark_worker":
        allowed.add("model_reasoning_summary")
    unexpected = set(data) - allowed
    if unexpected:
        errors.append("unexpected template settings: " + ", ".join(sorted(unexpected)))
    for key, expected in (("name", name), ("model", model), ("model_reasoning_effort", effort)):
        if data.get(key) != expected:
            errors.append(f"{key} must be {expected}")
    for key in ("description", "developer_instructions"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f"{key} must be nonempty")
    if name == "codex_spark_worker" and data.get("model_reasoning_summary") != "none":
        errors.append("optional Spark template needs its summary compatibility setting")
    return errors


def main() -> int:
    errors = []
    for filename in ("project-config.toml", *ROLE_CONFIGS):
        try:
            with (ROOT / "templates" / filename).open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, ValueError) as exc:
            errors.append(f"{filename}: {exc}")
            continue
        if filename == "project-config.toml":
            found = project_config_errors(data)
        else:
            role, model, effort = ROLE_CONFIGS[filename]
            found = role_config_errors(data, name=role, model=model, effort=effort)
        errors.extend(f"{filename}: {item}" for item in found)
    if any(not set(route).issubset(GPT_ALLOWLIST) for route in TASK_ROUTES.values()):
        errors.append("default routes must use the three default GPT models")
    if TASK_ROUTES["small"] != ("gpt-6-astra",):
        errors.append("legacy Astra simple-task route must stay with the parent")
    contract = (ROOT / "templates" / "long-horizon-contract.md").read_text()
    example = re.search(r"```json\s+(.*?)\n```", contract, re.S)
    if example is None:
        errors.append("missing executable brief example")
    else:
        try:
            errors.extend(f"example: {item}" for item in validate_brief(json.loads(example.group(1)), base_dir=ROOT))
        except ValueError as exc:
            errors.append(f"example: {exc}")
    for path in ROOT.rglob("*.md"):
        if "dist" in path.relative_to(ROOT).parts or path.name.startswith("RELEASE-"):
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", path.read_text()):
            target = target.split("#", 1)[0]
            if target and "://" not in target and not (path.parent / target).exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link {target}")
    for error in errors:
        print("FAIL: " + error)
    if not errors:
        print("OK: configuration, default routes, local links and brief example")
        print("Scope: local checks only; runtime and task completion are separate.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
