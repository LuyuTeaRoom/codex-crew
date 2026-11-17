"""Focused regressions for routing, bounded briefs and current evidence."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import policy_contract as policy
from validate import project_config_errors, role_config_errors


def initial_brief():
    return {
        "task_id": "task-a", "revision": 2,
        "scope": ["source.txt"], "do_not_touch": ["unrelated files"],
        "expected_result": "Check the source against criterion A1",
        "acceptance": {"A1": "The source contains the required value"},
        "context": {"goal": "Check one source", "verified_facts": [],
                    "artifact_index": [], "decisions": [], "open_items": [], "validation": []},
    }


class StartupTests(unittest.TestCase):
    def test_astra_primary_has_no_effort_gate(self):
        for source in ("current_runtime", "current_user_statement"):
            for effort in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "future_effort"):
                with self.subTest(source=source, effort=effort):
                    self.assertTrue(policy.crew_start_allowed(model="gpt-6-astra", effort=effort, source=source))

    def test_sol_primary_has_no_effort_gate_but_requires_current_evidence(self):
        for effort in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            for source in ("current_runtime", "current_rollout", "current_user_statement"):
                self.assertTrue(policy.crew_start_allowed(model="gpt-6.1-sol", effort=effort, source=source))
        for source in (None, "default_config", "historical_config"):
            self.assertFalse(policy.crew_start_allowed(model="gpt-6.1-sol", effort="low", source=source))

    def test_unknown_and_other_models_do_not_start(self):
        for model, effort in ((None, "max"), ("gpt-6-sol", "xhigh"),
                              ("gpt-6-luna", "max")):
            with self.subTest(model=model, effort=effort):
                self.assertFalse(policy.crew_start_allowed(model=model, effort=effort, source="current_runtime"))

    def test_historical_or_missing_evidence_does_not_start(self):
        for source in (None, "", "historical_config", "model_guess", "default_config"):
            with self.subTest(source=source):
                self.assertFalse(policy.crew_start_allowed(model="gpt-6-astra", effort="max", source=source))

    def test_new_dispatch_rechecks_current_selection(self):
        selections = ("xhigh", "low", None, "high")
        self.assertEqual([policy.crew_start_allowed(model="gpt-6-astra", effort=e, source="current_runtime")
                          for e in selections], [True, True, True, True])

    def test_deterministic_work_needs_no_model(self):
        self.assertEqual(policy.dispatch_candidates(task_kind="deterministic", available_models=policy.GPT_ALLOWLIST), ())

    def test_role_brief_uses_existing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            for role in ("investigator", "implementer", "reviewer"):
                brief = initial_brief()
                brief["context"]["decisions"] = [f"Work role: {role}; only touch assigned scope"]
                self.assertEqual(policy.validate_brief(brief, base_dir=Path(directory)), [])
                brief["role"] = role
                self.assertTrue(policy.validate_brief(brief, base_dir=Path(directory)))

    def test_bounded_escalation_respects_available_models(self):
        for available, expected in ((policy.GPT_ALLOWLIST, "gpt-6-luna"),
                                    ({"gpt-6.1-sol", "gpt-6-astra"}, "gpt-6.1-sol")):
            self.assertEqual(policy.dispatch_candidates(task_kind="bounded", available_models=available)[0], expected)


class AvailabilityTests(unittest.TestCase):
    def test_usage_semantics_and_unknown(self):
        self.assertIsNone(policy.remaining_percent(None))
        for used, remaining in ((0, 100), (1, 99), (100, 0)):
            self.assertEqual(policy.remaining_percent(used), remaining)
        self.assertFalse(policy.quota_exhausted(used_percent=1, error_code=None, same_model_canary=None))
        self.assertTrue(policy.quota_exhausted(used_percent=100, resets_at=2_000_000_000, error_code=None, same_model_canary=None))

    def test_quota_requires_corroboration(self):
        self.assertFalse(policy.quota_exhausted(used_percent=None, error_code="429", same_model_canary=None))
        self.assertFalse(policy.quota_exhausted(used_percent=None, error_code="insufficient_quota", same_model_canary=None))
        self.assertTrue(policy.quota_exhausted(used_percent=None, error_code="insufficient_quota", same_model_canary="QUOTA_EXHAUSTED"))

    def test_failure_categories(self):
        for message, expected in (
            ("Unsupported parameter: reasoning.summary", "MODEL_PARAMETER_INCOMPATIBLE"),
            ("Unknown model gpt-x for spawn_agent", "MODEL_OVERRIDE_NOT_EXPOSED"),
            ("HTTP 429", "RATE_LIMITED"), ("unauthorized 401", "AUTH_FAILURE"),
            ("connection timeout", "NETWORK_FAILURE"), ("test failed", "TASK_FAILURE"),
        ):
            with self.subTest(message=message):
                self.assertEqual(policy.classify_failure(error_code=None, message=message), expected)

    def test_optional_endpoint_diagnostic(self):
        self.assertEqual(policy.endpoint_preflight_status(max_output_tokens_exposed=False, requested_max_output_tokens=None, response_created=None), "NOT_APPLICABLE")
        self.assertEqual(policy.endpoint_preflight_status(max_output_tokens_exposed=True, requested_max_output_tokens=2, response_created=True), "FAIL")
        self.assertEqual(policy.endpoint_preflight_status(max_output_tokens_exposed=True, requested_max_output_tokens=1, response_created=True), "PASS")

    def test_reset_clears_unavailable_cache(self):
        old = {"gpt-6-luna": "RATE_LIMITED"}
        self.assertEqual(policy.clear_unavailable_cache(user_reported_upgrade_or_reset=True, cache=old), {})
        self.assertEqual(old, {"gpt-6-luna": "RATE_LIMITED"})

    def test_canary_requires_success_same_task_and_exact_path(self):
        e = policy.CanaryEvidence("task-a", "2026-09-14", "gpt-6-luna", "spawn/codex_luna_max_worker/max/read-only", None, {}, "MODEL_REACHABLE", "current task")
        kwargs = dict(task_id=e.task_id, model=e.model, call_path=e.call_path)
        self.assertTrue(policy.canary_reusable(e, **kwargs))
        for changed in (replace(e, task_id="task-b"), replace(e, model="gpt-6.1-sol"), replace(e, call_path="spawn/other/xhigh/read-only"), replace(e, result="FAIL"), replace(e, raw_error_code="429"), replace(e, ttl="previous task")):
            with self.subTest(evidence=changed):
                self.assertFalse(policy.canary_reusable(changed, **kwargs))


class RoutingTests(unittest.TestCase):
    def test_default_allowlist_and_candidate_order_exclude_legacy_models(self):
        expected_allowlist = {"gpt-6-astra", "gpt-6-luna", "gpt-6.1-sol"}
        self.assertEqual(policy.GPT_ALLOWLIST, expected_allowlist)

        legacy_models = {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}
        available_models = expected_allowlist | legacy_models
        expected_routes = {
            "deterministic": (),
            "bounded": ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"),
            "small": ("gpt-6-astra",),
            "high_risk": ("gpt-6-astra",),
            "medium": ("gpt-6.1-sol", "gpt-6-astra"),
            "verification": ("gpt-6.1-sol", "gpt-6-astra"),
            "batch": ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"),
        }
        self.assertEqual(policy.TASK_ROUTES, expected_routes)
        for task_kind, expected in expected_routes.items():
            with self.subTest(task_kind=task_kind):
                self.assertEqual(policy.dispatch_candidates(task_kind=task_kind, available_models=available_models), expected)
                self.assertEqual(policy.dispatch_candidates(task_kind=task_kind, available_models=legacy_models), ())

    def test_sol_routes_use_current_parent_without_astra_fallback(self):
        expected = {"deterministic": (), "bounded": ("gpt-6-luna", "gpt-6.1-sol"),
                    "batch": ("gpt-6-luna", "gpt-6.1-sol"), "small": ("gpt-6.1-sol",),
                    "medium": ("gpt-6.1-sol",), "verification": ("gpt-6.1-sol",),
                    "high_risk": ("gpt-6.1-sol",)}
        for kind, route in expected.items():
            self.assertEqual(policy.dispatch_candidates(task_kind=kind, available_models=policy.GPT_ALLOWLIST,
                                                       coordinator_model="gpt-6.1-sol"), route)
            self.assertEqual(policy.dispatch_candidates(task_kind=kind, available_models={"gpt-6-astra"},
                                                       coordinator_model="gpt-6.1-sol"), ())
        for stage in policy.LONG_HORIZON_MODELS:
            self.assertEqual(policy.long_horizon_model(stage, coordinator_model="gpt-6.1-sol"),
                             "gpt-6-luna" if stage == "bulk" else "gpt-6.1-sol")
        with self.assertRaises(ValueError):
            policy.dispatch_candidates(task_kind="bounded", available_models=policy.GPT_ALLOWLIST,
                                       coordinator_model="gpt-6-sol")

    def test_unavailable_worker_paths_preserve_candidate_filtering(self):
        cases = (
            ("batch", {"gpt-6-luna", "gpt-6-astra"}, ("gpt-6-luna", "gpt-6-astra")),
            ("batch", {"gpt-6.1-sol", "gpt-6-astra"}, ("gpt-6.1-sol", "gpt-6-astra")),
            ("medium", {"gpt-6-luna", "gpt-6-astra"}, ("gpt-6-astra",)),
            ("verification", {"gpt-6-luna"}, ()),
        )
        for task_kind, available, expected in cases:
            with self.subTest(task_kind=task_kind, available=available):
                self.assertEqual(policy.dispatch_candidates(task_kind=task_kind, available_models=available), expected)

    def test_long_horizon_stage_models(self):
        expected = {
            "scope": "gpt-6-astra",
            "decision": "gpt-6-astra",
            "bulk": "gpt-6-luna",
            "implementation": "gpt-6.1-sol",
            "verification": "gpt-6.1-sol",
        }
        self.assertEqual(policy.LONG_HORIZON_MODELS, expected)
        for stage, model in expected.items():
            with self.subTest(stage=stage):
                self.assertEqual(policy.long_horizon_model(stage), model)

    def test_nondefault_models_require_explicit_authorization(self):
        self.assertFalse(policy.external_model_allowed(model="deepseek/deepseek-v4-flash"))
        self.assertTrue(policy.external_model_allowed(model="deepseek/deepseek-v4-flash", current_task_explicit_providers=["deepseek"]))
        self.assertFalse(policy.external_model_allowed(model="gpt-5.3-codex-spark"))
        self.assertTrue(policy.external_model_allowed(model="gpt-5.3-codex-spark", current_task_explicit_models=["gpt-5.3-codex-spark"]))

    def test_dependent_or_overlapping_work_is_serial(self):
        self.assertFalse(policy.may_parallelize(independent=False, overlapping_writes=False))
        self.assertFalse(policy.may_parallelize(independent=True, overlapping_writes=True))
        self.assertTrue(policy.may_parallelize(independent=True, overlapping_writes=False))


class ConfigurationTests(unittest.TestCase):
    def test_project_preserves_parent_choice(self):
        with (ROOT / "templates/project-config.toml").open("rb") as stream:
            data = tomllib.load(stream)
        self.assertEqual(project_config_errors(data), [])
        for key, value in (("model_reasoning_effort", "xhigh"), ("model", "gpt-6-astra"), ("approval_policy", "never")):
            self.assertTrue(project_config_errors({**data, key: value}))

    def test_luna_role_is_gpt6_luna_max(self):
        with (ROOT / "templates/agent-luna.toml").open("rb") as stream:
            data = tomllib.load(stream)
        expected = dict(name="codex_luna_max_worker", model="gpt-6-luna", effort="max")
        self.assertEqual(role_config_errors(data, **expected), [])
        for key, value in (("model", "gpt-6.1-sol"), ("model_reasoning_effort", "xhigh"), ("name", "luna_worker")):
            with self.subTest(key=key):
                self.assertTrue(role_config_errors({**data, key: value}, **expected))

    def test_sol_role_is_gpt6_sol_xhigh(self):
        with (ROOT / "templates/agent-sol.toml").open("rb") as stream:
            data = tomllib.load(stream)
        expected = dict(name="codex_sol_xhigh_worker", model="gpt-6.1-sol", effort="xhigh")
        self.assertEqual(role_config_errors(data, **expected), [])
        for key, value in (("model", "gpt-6-luna"), ("model_reasoning_effort", "max")):
            with self.subTest(key=key):
                self.assertTrue(role_config_errors({**data, key: value}, **expected))

    def test_role_templates_reject_unrequested_runtime_overrides(self):
        with (ROOT / "templates/agent-sol.toml").open("rb") as stream:
            data = tomllib.load(stream)
        for key, value in (("approval_policy", "never"), ("sandbox_mode", "danger-full-access"), ("model_provider", "unapproved-provider")):
            with self.subTest(key=key):
                self.assertTrue(role_config_errors({**data, key: value}, name="codex_sol_xhigh_worker", model="gpt-6.1-sol", effort="xhigh"))


class BriefTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="crew-unit-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "source.txt").write_text("synthetic fixture value=3\n")
        (self.workspace / "check.log").write_text("synthetic test evidence\n")
        self.brief = initial_brief()
        packet = self.brief["context"]
        packet["artifact_index"] = [
            {"id": name, "path": path, "range": "line 1", "version": policy.file_version(self.workspace / path)}
            for name, path in (("source", "source.txt"), ("log", "check.log"))
        ]
        packet["verified_facts"] = [{"text": "The fixture contains value=3", "sources": ["source"]}]
        self.record = {
            "task_id": "task-a", "revision": 2, "criterion": "A1", "status": "PASS",
            "method": "read fixture value", "inputs": ["source"],
            "input_versions": {"source": packet["artifact_index"][0]["version"]},
            "evidence": ["log"], "conditions": {"reader": "text"},
        }
        packet["validation"] = [self.record]

    def errors(self, brief=None, **kwargs):
        return policy.validate_brief(self.brief if brief is None else brief, base_dir=self.workspace, **kwargs)

    def test_valid_initial_and_evidence_backed_briefs(self):
        self.assertEqual(self.errors(initial_brief()), [])
        self.assertEqual(self.errors(), [])

    def test_all_null_context_is_rejected(self):
        self.brief["context"] = dict.fromkeys(policy.HANDOFF_PACKET_FIELDS)
        self.assertTrue(self.errors())

    def test_large_text_inside_legal_field_is_rejected(self):
        self.brief["context"]["goal"] = "x" * 200_000
        self.assertEqual(self.errors()[0]["field"], "$")

    def test_extra_transcript_field_is_rejected(self):
        self.brief["context"]["full_transcript"] = "history"
        self.assertTrue(self.errors())

    def test_limit_is_configurable_and_counts_whole_brief(self):
        size = len(json.dumps(self.brief, ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(self.errors(max_chars=size), [])
        self.assertTrue(self.errors(max_chars=size - 1))
        self.brief["do_not_touch"].append("constraint" * 2000)
        self.assertTrue(self.errors())

    def test_missing_evidence_file_is_rejected(self):
        (self.workspace / "check.log").unlink()
        self.assertTrue(any(e["field"].endswith(".path") for e in self.errors()))

    def test_uncommitted_source_change_invalidates_packet(self):
        (self.workspace / "source.txt").write_text("value=4\n")
        self.assertTrue(any(e["field"].endswith(".version") for e in self.errors()))

    def test_updating_index_cannot_refresh_old_check(self):
        (self.workspace / "source.txt").write_text("value=4\n")
        self.brief["context"]["artifact_index"][0]["version"] = policy.file_version(self.workspace / "source.txt")
        self.assertTrue(any(e["field"].endswith(".input_versions") for e in self.errors()))

    def test_fact_without_source_is_rejected(self):
        self.brief["context"]["verified_facts"][0]["sources"] = []
        self.assertTrue(self.errors())

    def test_unknown_reference_is_rejected(self):
        self.record["evidence"] = ["missing-id"]
        self.assertTrue(self.errors())

    def test_pass_requires_executed_method_inputs_and_evidence(self):
        for field in ("method", "inputs", "input_versions", "evidence"):
            brief = copy.deepcopy(self.brief)
            del brief["context"]["validation"][0][field]
            with self.subTest(field=field):
                self.assertTrue(self.errors(brief))

    def test_unexecuted_check_can_be_recorded_honestly(self):
        self.brief["context"]["validation"] = [{"criterion": "A1", "status": "NOT_RUN", "reason": "not executed yet"}]
        self.assertEqual(self.errors(), [])

    def test_old_task_revision_and_wrong_criterion_are_rejected(self):
        for field, value in (("revision", 1), ("task_id", "task-b"), ("criterion", "A2")):
            brief = copy.deepcopy(self.brief)
            brief["context"]["validation"][0][field] = value
            with self.subTest(field=field):
                self.assertTrue(self.errors(brief))

    def test_duplicate_current_verdict_is_rejected(self):
        self.brief["context"]["validation"].append(copy.deepcopy(self.record))
        self.assertTrue(self.errors())

    def test_invalid_field_types_return_errors(self):
        for field, value in (("revision", True), ("scope", "all files"), ("acceptance", []), ("context", None)):
            brief = copy.deepcopy(self.brief)
            brief[field] = value
            with self.subTest(field=field):
                self.assertTrue(self.errors(brief))
        self.assertTrue(policy.validate_brief([], base_dir=self.workspace))

    def test_reuse_requires_known_matching_dependencies_and_conditions(self):
        kwargs = dict(task_id="task-a", revision=2, input_versions=self.record["input_versions"], method=self.record["method"], conditions=self.record["conditions"], dependencies_known=True)
        self.assertTrue(policy.validation_reusable(self.record, **kwargs))
        for field, value in (("task_id", "task-b"), ("revision", 3), ("input_versions", {"source": "sha256:changed"}), ("method", "different check"), ("conditions", {"reader": "different"}), ("dependencies_known", False)):
            with self.subTest(field=field):
                self.assertFalse(policy.validation_reusable(self.record, **{**kwargs, field: value}))
        self.assertFalse(policy.validation_reusable({**self.record, "status": "FAIL"}, **kwargs))

    def test_old_result_does_not_change_files(self):
        before = (self.workspace / "source.txt").read_bytes()
        self.assertFalse(policy.result_is_current({"task_id": "task-a", "revision": 1}, task_id="task-a", revision=2))
        self.assertEqual((self.workspace / "source.txt").read_bytes(), before)

    def test_cli_checks_real_payload_and_malformed_json(self):
        path = self.workspace / "brief.json"
        path.write_text(json.dumps(self.brief))
        cmd = [sys.executable, "-B", str(ROOT / "scripts/policy_contract.py"), str(path), "--base-dir", str(self.workspace)]
        valid = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr + valid.stdout)
        self.assertFalse(json.loads(valid.stdout)["task_completion_verified"])
        path.write_text(" " * 12_000 + json.dumps(initial_brief()))
        oversized = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(oversized.returncode, 1)
        self.assertFalse(json.loads(oversized.stdout)["packet_valid"])
        path.write_text("{broken")
        invalid = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertFalse(json.loads(invalid.stdout)["packet_valid"])

    def test_cli_rejects_ambiguous_duplicate_fields(self):
        path = self.workspace / "duplicate.json"
        source = json.dumps(initial_brief())
        for old, new in (
            ('"scope": [', '"scope": ["outside.txt"], "scope": ['),
            ('"A1": ', '"A1": "different requirement", "A1": '),
        ):
            with self.subTest(field=old):
                path.write_text(source.replace(old, new, 1))
                result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/policy_contract.py"), str(path), "--base-dir", str(self.workspace)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 1)
                data = json.loads(result.stdout)
                self.assertFalse(data["packet_valid"])
                self.assertIn("duplicate JSON key", data["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
