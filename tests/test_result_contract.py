"""Acceptance-bound return checks, using actual temporary files and CLI calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import policy_contract as policy


class ResultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="crew-result-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        for name in ("a", "b", "log"):
            (self.workspace / name).write_text(name + " original")
        self.artifacts = [dict(id=n, path=n, range="all", version=policy.file_version(self.workspace / n)) for n in ("a", "b", "log")]
        self.brief = dict(task_id="repair", revision=2, scope=["a"], do_not_touch=["b"], expected_result="repair a; preserve b",
                          acceptance={"A1": "a repaired", "A2": "b preserved"}, context=dict(goal="repair", verified_facts=[], artifact_index=copy.deepcopy(self.artifacts), decisions=[], open_items=[], validation=[]))
        self.result = dict(task_id="repair", revision=2, brief_version=policy.brief_version(self.brief), status="PASS", summary="repaired", actual_changes=["a"], artifact_index=copy.deepcopy(self.artifacts), validation=[self.record("A1", "a"), self.record("A2", "b")], unfinished=[])
        self.assertEqual(policy.validate_brief(self.brief, base_dir=self.workspace), [])

    def record(self, criterion, source):
        return dict(task_id="repair", revision=2, criterion=criterion, status="PASS", method="read " + source, inputs=[source], input_versions={source: policy.file_version(self.workspace / source)}, evidence=["log"], conditions={"reader": "UTF-8"})

    def errors(self, result=None):
        return policy.validate_result(self.result if result is None else result, brief=self.brief, base_dir=self.workspace)

    def test_complete_return_and_content_fingerprint(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(policy.brief_version(self.brief), policy.brief_version(dict(reversed(list(self.brief.items())))))

    def test_pass_with_missing_acceptance_rejected(self):
        self.result["validation"].pop()
        self.assertTrue(any("missing acceptance" in e["message"] for e in self.errors()))

    def test_summary_cannot_replace_original_constraints_or_acceptance(self):
        for field, value in (("do_not_touch", []), ("acceptance", {"A1": "a repaired"})):
            with self.subTest(field=field):
                shortened = copy.deepcopy(self.brief)
                shortened[field] = value
                self.result["brief_version"] = policy.brief_version(shortened)
                self.assertTrue(any(e["field"] == "brief_version" for e in self.errors()))

    def test_old_result_rejected_without_undoing_existing_writes(self):
        (self.workspace / "a").write_text("old worker already wrote this")
        self.result["revision"] = 1
        self.result["artifact_index"][0]["version"] = policy.file_version(self.workspace / "a")
        self.assertTrue(any(e["field"] == "revision" for e in self.errors()))
        self.assertEqual((self.workspace / "a").read_text(), "old worker already wrote this")

    def test_legitimate_edit_accepts_fresh_check_without_revalidating_preedit_hash(self):
        (self.workspace / "a").write_text("repaired")
        self.result["artifact_index"][0]["version"] = policy.file_version(self.workspace / "a")
        self.result["validation"][0] = self.record("A1", "a")
        self.assertEqual(self.errors(), [])
        self.assertTrue(policy.validate_brief(self.brief, base_dir=self.workspace))

    def test_local_repair_invalidates_only_affected_check(self):
        old = copy.deepcopy(self.result["validation"])
        (self.workspace / "a").write_text("repaired")
        for record, source, expected in ((old[0], "a", False), (old[1], "b", True)):
            self.assertEqual(policy.validation_reusable(record, task_id="repair", revision=2, input_versions={source: policy.file_version(self.workspace / source)}, method=record["method"], conditions=record["conditions"], dependencies_known=True), expected)
        self.result["artifact_index"][0]["version"] = policy.file_version(self.workspace / "a")
        self.assertTrue(self.errors())
        self.result["validation"][0] = self.record("A1", "a")
        self.assertEqual(self.result["validation"][1], old[1])
        self.assertEqual(self.errors(), [])

    def test_missing_and_modified_evidence_rejected(self):
        (self.workspace / "log").write_text("changed log")
        self.assertTrue(self.errors())
        (self.workspace / "log").unlink()
        self.assertTrue(self.errors())

    def test_executed_checks_need_method_versions_evidence_conditions(self):
        for field in ("method", "input_versions", "evidence", "conditions"):
            result = copy.deepcopy(self.result)
            del result["validation"][0][field]
            with self.subTest(field=field):
                self.assertTrue(self.errors(result))

    def test_duplicate_unknown_and_stale_checks_rejected(self):
        for change in ({"criterion": "A2"}, {"criterion": "A9"}, {"revision": 1}, {"task_id": "other"}):
            result = copy.deepcopy(self.result)
            result["validation"][0].update(change)
            self.assertTrue(self.errors(result))

    def test_pass_cannot_hide_unfinished_or_failed_checks(self):
        for change in ({"unfinished": ["still working"]}, {"validation": [self.record("A1", "a"), {"criterion": "A2", "status": "NOT_RUN", "reason": "pending"}]}):
            self.assertTrue(self.errors({**self.result, **change}))

    def test_partial_failed_and_blocked_reports_can_be_valid(self):
        for status, check in (("PARTIAL", "NOT_RUN"), ("BLOCKED", "BLOCKED"), ("FAIL", "FAIL")):
            result = copy.deepcopy(self.result)
            result.update(status=status, unfinished=["A2 incomplete"])
            if check == "FAIL":
                result["validation"][1]["status"] = check
            else:
                result["validation"][1] = dict(criterion="A2", status=check, reason="fixture issue")
            self.assertEqual(self.errors(result), [])
            result["unfinished"] = []
            self.assertTrue(self.errors(result))

    def test_bad_types_extra_fields_and_size_rejected(self):
        for result in ([], {**self.result, "acceptance": {}}, {**self.result, "revision": True}, {**self.result, "artifact_index": None}, {**self.result, "summary": "x" * 12000}, {**self.result, "validation": [None]}):
            self.assertTrue(self.errors(result))

    def test_cli_checks_result_duplicate_keys_and_completion_boundary(self):
        brief = self.workspace / "brief.json"
        returned = self.workspace / "result.json"
        brief.write_text(json.dumps(self.brief))
        command = [sys.executable, "-B", str(ROOT / "scripts/policy_contract.py"), str(brief), "--result", str(returned), "--base-dir", str(self.workspace)]
        for raw, expected in ((json.dumps(self.result), 0), (json.dumps(self.result).replace('"revision": 2', '"revision": 1, "revision": 2', 1), 1), ("{broken", 1), (" " * 12001, 1)):
            returned.write_text(raw)
            run = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
            data = json.loads(run.stdout)
            self.assertEqual(data["result_valid"], expected == 0)
            self.assertFalse(data["task_completion_verified"])


if __name__ == "__main__":
    unittest.main()
