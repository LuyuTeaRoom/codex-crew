import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SINGLE_ITEM = ROOT / "scripts" / "single_item.py"
MEASURE_USAGE = ROOT / "scripts" / "measure_usage.py"
MANAGER = Path.home() / ".codex" / "process_manager" / "task_process.py"


def event(kind, turn_id=None, *, timestamp="2026-11-17T00:00:00Z", **payload):
    body = {"type": kind, **payload}
    if turn_id is not None:
        body["turn_id"] = turn_id
    return {"timestamp": timestamp, "type": "event_msg", "payload": body}


def usage(input_tokens=0, cached_input_tokens=0, output_tokens=0,
          reasoning_output_tokens=0, total_tokens=None):
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class MeasureUsageTests(unittest.TestCase):
    def test_response_ids_dedupe_retries_keep_role_split_and_missing_usage_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            coordinator = directory / "coordinator.jsonl"
            worker = directory / "worker.jsonl"
            duplicate_only = directory / "duplicate-only.jsonl"
            first = {"input_tokens": 100, "cached_input_tokens": 30, "output_tokens": 20,
                     "reasoning_output_tokens": 8, "total_tokens": 120}
            retry = {"input_tokens": 12, "cached_input_tokens": 2, "output_tokens": 4,
                     "reasoning_output_tokens": 1, "total_tokens": 16}
            write_jsonl(coordinator, [
                event("task_started", "turn-a"),
                {"timestamp": "2026-11-17T00:00:01Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-a", "response_id": "resp-1", "usage": first}},
                {"timestamp": "2026-11-17T00:00:02Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-a", "response_id": "resp-1", "usage": first}},
                {"timestamp": "2026-11-17T00:00:03Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-a", "response_id": "resp-retry", "usage": retry}},
                event("task_complete", "turn-a", timestamp="2026-11-17T00:00:04Z"),
            ])
            write_jsonl(worker, [
                event("task_started", "turn-b"),
                {"timestamp": "2026-11-17T00:00:05Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-b", "response_id": "resp-1", "usage": first}},
                {"timestamp": "2026-11-17T00:00:06Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-b", "response_id": "resp-2"}},
                event("task_complete", "turn-b", timestamp="2026-11-17T00:00:07Z"),
            ])
            write_jsonl(duplicate_only, [
                event("task_started", "turn-c"),
                {"timestamp": "2026-11-17T00:00:08Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-c", "response_id": "resp-1"}},
                {"timestamp": "2026-11-17T00:00:08Z", "type": "response_item", "payload": {
                    "type": "message", "turn_id": "turn-c", "response_id": "resp-retry", "usage": retry}},
                event("task_complete", "turn-c", timestamp="2026-11-17T00:00:09Z"),
            ])
            source_file = directory / "sources.json"
            source_file.write_text(json.dumps({"sources": [
                {"path": "coordinator.jsonl", "thread_id": "thread-c", "turn_id": "turn-a", "role": "coordinator"},
                {"path": "worker.jsonl", "thread_id": "thread-w", "turn_id": "turn-b", "role": "worker"},
                {"path": "duplicate-only.jsonl", "thread_id": "thread-d", "turn_id": "turn-c", "role": "coordinator"},
            ]}))
            output = directory / "report.json"
            run = subprocess.run(
                [sys.executable, "-B", str(MEASURE_USAGE), "--source-json", str(source_file), "--output", str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["turn_state"], "complete")
            self.assertEqual(report["roles"]["coordinator"]["turn_state"], "complete")
            self.assertEqual(report["roles"]["worker"]["turn_state"], "complete")
            self.assertEqual(report["sources"][0]["event_count"], 2)
            self.assertEqual(report["sources"][0]["usage"]["input_tokens"], 112)
            self.assertEqual(report["sources"][0]["usage"]["cached_input_tokens"], 32)
            self.assertEqual(report["sources"][0]["usage"]["non_cached_input_tokens"], 80)
            self.assertEqual(report["sources"][0]["usage"]["output_tokens"], 24)
            self.assertEqual(report["sources"][0]["usage"]["reasoning_output_tokens"], 9)
            self.assertEqual(report["sources"][1]["event_count"], 1)
            self.assertEqual(report["sources"][1]["raw_event_count"], 2)
            self.assertIsNone(report["sources"][1]["usage"]["input_tokens"])
            self.assertEqual(report["sources"][1]["duplicate_response_ids"], ["resp-1"])
            self.assertIsNone(report["sources"][1]["usage"]["output_tokens"])
            self.assertEqual(report["sources"][2]["event_count"], 0)
            self.assertEqual(report["sources"][2]["usage"]["input_tokens"], 0)
            self.assertEqual(report["sources"][2]["usage"]["cached_input_tokens"], 0)
            self.assertEqual(report["sources"][2]["duplicate_response_ids"], ["resp-1", "resp-retry"])
            self.assertEqual([item["response_id"] for item in report["deduplication"]["native_response_id_duplicates"]], ["resp-1", "resp-1", "resp-retry"])
            self.assertEqual(report["deduplication"]["cumulative_sources_without_response_ids"], 0)

    def test_cumulative_deltas_match_turn_and_do_not_make_up_response_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rollout = directory / "rollout.jsonl"
            write_jsonl(rollout, [
                event("task_started", "old-turn"),
                event("token_count", "old-turn", info={"total_token_usage": usage(100, 40, 20, 5)}),
                event("task_complete", "old-turn"),
                event("task_started", "target-turn"),
                event("token_count", "target-turn", info={"total_token_usage": usage(125, 50, 28, 7)}),
                event("task_complete", "target-turn", timestamp="2026-11-17T00:00:08Z"),
            ])
            sources = directory / "sources.json"
            sources.write_text(json.dumps({"sources": [{
                "path": "rollout.jsonl", "thread_id": "thread-1", "turn_id": "target-turn", "role": "coordinator",
            }]}))
            output = directory / "report.json"
            run = subprocess.run(
                [sys.executable, "-B", str(MEASURE_USAGE), "--source-json", str(sources), "--output", str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(output.read_text())
            source = report["sources"][0]
            self.assertEqual(source["turn_state"], "complete")
            self.assertEqual(source["method"], "cumulative_total_delta")
            self.assertEqual(source["usage"]["input_tokens"], 25)
            self.assertEqual(source["usage"]["cached_input_tokens"], 10)
            self.assertEqual(source["usage"]["output_tokens"], 8)
            self.assertEqual(source["usage"]["reasoning_output_tokens"], 2)
            self.assertEqual(source["usage"]["total_tokens"], 33)
            self.assertNotIn("response_id", source["events"][0])
            self.assertIn("No response id is fabricated", report["accounting"]["cumulative_source_note"])

    def test_unfinished_target_turn_stays_snapshot_when_a_later_turn_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rollout = directory / "rollout.jsonl"
            write_jsonl(rollout, [
                event("task_started", "target"),
                event("token_count", "target", info={"total_token_usage": usage(40, 15, 9, 3)}),
                event("task_started", "later"),
                event("token_count", "later", info={"total_token_usage": usage(55, 20, 12, 4)}),
                event("task_complete", "later"),
            ])
            source = directory / "source.json"
            source.write_text(json.dumps({"sources": [{
                "path": "rollout.jsonl", "thread_id": "thread", "turn_id": "target", "role": "coordinator",
            }]}))
            output = directory / "report.json"
            run = subprocess.run(
                [sys.executable, "-B", str(MEASURE_USAGE), "--source-json", str(source), "--output", str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(output.read_text())
            target = report["sources"][0]
            self.assertEqual(report["turn_state"], "snapshot")
            self.assertEqual(target["turn_state"], "snapshot")
            self.assertIsNone(target["completion_kind"])
            self.assertEqual(target["usage"]["input_tokens"], 40)
            self.assertEqual(target["usage"]["output_tokens"], 9)

    def test_finished_turn_without_usage_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rollout = directory / "rollout.jsonl"
            write_jsonl(rollout, [event("task_started", "done"), event("task_complete", "done")])
            source = directory / "source.json"
            source.write_text(json.dumps({"sources": [{
                "path": "rollout.jsonl", "thread_id": "thread", "turn_id": "done", "role": "worker",
            }]}))
            output = directory / "report.json"
            run = subprocess.run(
                [sys.executable, "-B", str(MEASURE_USAGE), "--source-json", str(source), "--output", str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["turn_state"], "complete")
            self.assertTrue(all(value is None for value in report["sources"][0]["usage"].values()))

    def test_invalid_cached_and_reasoning_subsets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = (
                ("cached", usage(10, 11, 20, 5), "cached_input_tokens exceeds input_tokens"),
                ("reasoning", usage(10, 5, 20, 21), "reasoning_output_tokens exceeds output_tokens"),
            )
            for label, bad_usage, message in cases:
                with self.subTest(label=label):
                    rollout = directory / f"{label}.jsonl"
                    write_jsonl(rollout, [
                        event("task_started", "bad"),
                        {"timestamp": "2026-11-17T00:00:01Z", "type": "response_item", "payload": {
                            "type": "message", "turn_id": "bad", "response_id": f"bad-{label}", "usage": bad_usage}},
                        event("task_complete", "bad"),
                    ])
                    source = directory / f"{label}-source.json"
                    source.write_text(json.dumps({"sources": [{
                        "path": rollout.name, "thread_id": "thread", "turn_id": "bad", "role": "worker",
                    }]}))
                    run = subprocess.run(
                        [sys.executable, "-B", str(MEASURE_USAGE), "--source-json", str(source),
                         "--output", str(directory / f"{label}-report.json")],
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(run.returncode, 2)
                    self.assertIn(message, run.stderr)


@unittest.skipUnless(MANAGER.is_file(), "optional external process manager is not installed")
class SingleItemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "managed-jobs"
        self.env = dict(os.environ, CODEX_TASK_PROCESS_STORE=str(self.store))
        self.task_id = os.environ.get("CODEX_THREAD_ID", "test-thread")

    def invoke_run(self, directory, name, command, *, wait=5, timeout=6):
        directory.mkdir(parents=True, exist_ok=True)
        index = directory / "jobs.json"
        result = directory / "result.json"
        run = subprocess.run(
            [sys.executable, "-B", str(SINGLE_ITEM), "run",
             "--cwd", str(directory), "--task", self.task_id, "--turn", "test-turn",
             "--name", name, "--timeout", str(timeout), "--wait", str(wait),
             "--result", str(result), "--job-index", str(index), "--manager", str(MANAGER), "--", *command],
            capture_output=True, text=True, env=self.env, timeout=15,
        )
        data = json.loads(run.stdout) if run.stdout else {}
        return run, data, index

    def invoke_status(self, index, job_id):
        return subprocess.run(
            [sys.executable, "-B", str(SINGLE_ITEM), "status", "--job-id", job_id,
             "--job-index", str(index), "--manager", str(MANAGER)],
            capture_output=True, text=True, env=self.env, timeout=10,
        )

    def test_two_isolated_jobs_report_success_and_nonzero_exit(self):
        first_dir = self.root / "isolation-one"
        success, first, first_index = self.invoke_run(
            first_dir, "success", [sys.executable, "-c",
                                   "import json; open('result.json','w').write(json.dumps({'status':'DONE','summary':'ok'}))"],
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertEqual(first["exit_code"], 0)
        self.assertFalse(first["job"]["group_remaining"])
        self.assertEqual(first["result"]["state"], "available")
        self.assertEqual(first["result"]["summary"]["status"], "DONE")

        second_dir = self.root / "isolation-two"
        failure, second, second_index = self.invoke_run(
            second_dir, "nonzero", [sys.executable, "-c", "raise SystemExit(7)"],
        )
        self.assertEqual(failure.returncode, 1)
        self.assertEqual(second["status"], "FAILED")
        self.assertEqual(second["exit_code"], 7)
        self.assertEqual(second["assessment"], "PROCESS_FAILED")
        self.assertFalse(second["job"]["group_remaining"])
        self.assertEqual(second["result"]["state"], "missing")
        self.assertEqual(len(json.loads(first_index.read_text())["jobs"]), 1)
        self.assertEqual(len(json.loads(second_index.read_text())["jobs"]), 1)

    def test_bounded_wait_then_status_reuses_the_original_job(self):
        directory = self.root / "polling"
        run, initial, index = self.invoke_run(
            directory, "bounded", [sys.executable, "-c",
                                    "import time; time.sleep(.4); open('result.json','w').write('{\"status\":\"DONE\"}')"],
            wait=0, timeout=5,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        job_id = initial["job_id"]
        self.assertIn(initial["status"], {"RUNNING", "SUCCEEDED"})
        observed = initial
        deadline = time.monotonic() + 5
        while observed["status"] == "RUNNING" and time.monotonic() < deadline:
            status = self.invoke_status(index, job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            observed = json.loads(status.stdout)
        self.assertEqual(observed["status"], "SUCCEEDED")
        self.assertFalse(observed["job"]["group_remaining"])
        self.assertEqual(observed["job_id"], job_id)
        self.assertEqual(observed["result"]["state"], "available")
        index_data = json.loads(index.read_text())
        self.assertEqual(list(index_data["jobs"]), [job_id])


if __name__ == "__main__":
    unittest.main()
