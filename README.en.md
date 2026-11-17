# codex-crew 0.5.3

A lightweight Codex collaboration skill. Delegate complete deliverables, reuse the original worker for revisions, and let the primary model inspect actual outputs and continue the authorized task chain.

The current GPT-6 Astra or GPT-6.1 Sol primary session keeps the user's reasoning effort. Bounded work uses GPT-6 Luna/max; necessary independent complex work uses GPT-6.1 Sol/xhigh. Startup evidence and eligibility are maintained in [SKILL.md](SKILL.md). Models and roles are not a mandatory pipeline.

## Install

Place this repository in a local skill directory or link it from `~/.agents/skills/codex-crew`. Check existing entries and roles first. Merge only required [project settings](templates/project-config.toml) and [operating rules](templates/AGENTS.md), preserving permissions and user configuration. Worker templates are [Luna](templates/agent-luna.toml) and [Sol](templates/agent-sol.toml).

Workers read the original brief and [short contract](templates/worker-handoff.md); the primary verifies the returned contract and actual artifacts. Luna outputs receive the existing [Sol audit](templates/sol-audit.md). Detailed reports, usage measurement and additional independent review are enabled when requested or needed for the task's risk.

## Optional tools

`current_session.py` reads current-thread configuration evidence when needed; `policy_contract.py` checks brief/result contracts. On macOS/Linux, `single_item.py` requires an external `task_process.py` selected with `--manager`; that manager is not installed by this repository. `measure_usage.py` independently reads local rollout JSONL files and reports deduplicated usage, cached/uncached inputs and snapshot/complete status. See [tool usage](references/execution-tools.md).

## Checks

```sh
python3 -B scripts/validate.py
python3 -B -m unittest discover -s tests -v
```

Only external-manager integration cases are skipped when the manager is unavailable. See [verification](references/verification-guide.md) and [0.5.3 release notes](RELEASE-0.5.3.md).
