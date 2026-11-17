# codex-crew 0.5.3

- Allow both current GPT-6 Astra and GPT-6.1 Sol primaries without an additional reasoning-effort gate; keep evidence precedence and Worker identity checks.
- Consolidate model routing, delegation, continuation and audit instructions into their maintained sources.
- Keep the primary responsible for the full authorized objective, actual-artifact acceptance and the next task.
- Delegate complete bounded deliverables and reuse the original worker for targeted revisions.
- Make measurement, detailed reports and additional independent review conditional on user needs or concrete risk.
- Provide reusable single-item execution and offline usage tools; retain the brief/result contract and necessary Sol audit.
- Keep local personal-rule snapshots, machine-specific research, backups and historical test outputs out of this public package.

The single-item CLI uses an external process manager; the usage CLI reads existing local records without executing the task again. Validation results are recorded with the release.

Validation: 79 local regression tests passed, including the updated Astra startup cases and single-item execution checks. Configuration, routes, local links, brief example and skill format checks passed. Worker models and fixed efforts are unchanged.
