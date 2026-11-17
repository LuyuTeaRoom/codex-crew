"""Current-session identity, source precedence and read-only startup regression."""
from pathlib import Path
import json
import sqlite3
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from current_session import read_current_turn, resolve_settings


class CurrentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.log = self.home / 'rollout.jsonl'
        prefix = (json.dumps({'type': 'session_meta', 'payload': {'id': 'thread-a'}}) + '\n').encode()
        # Simulate inherited/old metadata, which must never supply current settings.
        prefix += (json.dumps({'type': 'turn_context', 'payload': {'turn_id': 'old', 'model': 'gpt-6-astra', 'effort': 'max'}}) + '\n').encode()
        self.offset = len(prefix)
        self.log.write_bytes(prefix + (json.dumps({'type': 'turn_context', 'payload': {'turn_id': 'turn-a', 'model': 'gpt-6-astra', 'effort': 'medium'}}) + '\n').encode())
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, source TEXT)')
            db.execute('INSERT INTO threads VALUES (?,?,?)', ('thread-a', str(self.log), 'vscode'))
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute('CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, status TEXT, rollout_byte_offset INTEGER)')
            db.execute('INSERT INTO thread_turns VALUES (?,?,?,?)', ('thread-a', 'turn-a', 'inProgress', self.offset))

    def session(self):
        return read_current_turn(self.home, 'thread-a')

    def test_reads_exact_active_turn_without_modifying_sources(self):
        before = {p: p.read_bytes() for p in self.home.iterdir()}
        result = resolve_settings(session_reader=self.session)
        self.assertTrue(result['crew_start_allowed'])
        self.assertEqual((result['model'], result['effort'], result['turn_id']), ('gpt-6-astra', 'medium', 'turn-a'))
        self.assertEqual(result['evidence']['byte_offset'], self.offset)
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.iterdir()})

    def test_runtime_astra_low_is_allowed_without_fallback(self):
        def forbidden():
            self.fail('must not read lower-priority source after a complete runtime result')
        result = resolve_settings(runtime={'model': 'gpt-6-astra', 'effort': 'low'}, session_reader=forbidden,
                                  user={'model': 'gpt-6-astra', 'effort': 'max'})
        self.assertTrue(result['crew_start_allowed'])
        self.assertEqual(result['source'], 'current_runtime')

    def test_partial_known_denial_cannot_be_overridden_by_fallback(self):
        for runtime in ({"model": "gpt-6-sol"}, {"primary": False}):
            with self.subTest(runtime=runtime):
                result = resolve_settings(runtime=runtime, session_reader=self.session)
                self.assertFalse(result['crew_start_allowed'])
                self.assertEqual(result['source'], 'current_runtime')

    def test_rollout_precedes_user_and_partial_runtime_does_not_mix(self):
        result = resolve_settings(runtime={'model': 'gpt-6-astra'}, session_reader=self.session,
                                  user={'model': 'gpt-6-astra', 'effort': 'max'})
        self.assertEqual(result['source'], 'current_runtime')
        self.assertTrue(result['crew_start_allowed'])
        self.assertIsNone(result.get('effort'))
        result = resolve_settings(session_reader=self.session,
                                  user={'model': 'gpt-6-astra', 'effort': 'max'})
        self.assertEqual((result['source'], result['effort']), ('current_rollout', 'medium'))

    def test_sol_model_alone_or_low_is_enough_without_fallback(self):
        for evidence in ({"model": "gpt-6.1-sol"}, {"model": "gpt-6.1-sol", "effort": "low"}):
            result = resolve_settings(runtime=evidence, session_reader=lambda: self.fail("no fallback needed"))
            self.assertTrue(result["crew_start_allowed"])
            self.assertEqual(result["source"], "current_runtime")
            self.assertEqual(result.get("effort"), evidence.get("effort"))

    def test_partial_runtime_effort_does_not_override_astra_or_block_sol(self):
        result = resolve_settings(runtime={"effort": "low"}, session_reader=self.session)
        self.assertFalse(result["crew_start_allowed"])
        self.assertEqual(result["source"], "unknown")
        result = resolve_settings(runtime={"effort": "low"},
                                  user={"model": "gpt-6.1-sol", "effort": "low"})
        self.assertTrue(result["crew_start_allowed"])
        result = resolve_settings(runtime={"model": "gpt-6-astra"}, user={"model": "gpt-6.1-sol"})
        self.assertTrue(result["crew_start_allowed"])
        self.assertEqual(result["model"], "gpt-6-astra")
        self.assertEqual(result["source"], "current_runtime")

    def test_sol_active_turn_and_leaf_identity(self):
        self.log.write_bytes(self.log.read_bytes()[:self.offset] +
            (json.dumps({"type": "turn_context", "payload": {"turn_id": "turn-a",
             "model": "gpt-6.1-sol", "effort": "low"}}) + "\n").encode())
        self.assertTrue(resolve_settings(session_reader=self.session)["crew_start_allowed"])
        with sqlite3.connect(self.home / "state_5.sqlite") as db:
            db.execute("UPDATE threads SET source=?", ('{"subagent":{}}',))
        self.assertFalse(resolve_settings(session_reader=self.session)["crew_start_allowed"])
        self.assertFalse(resolve_settings(primary=False, runtime={"model": "gpt-6.1-sol"})["crew_start_allowed"])

    def test_known_other_model_stops_resolution(self):
        result = resolve_settings(runtime={'model': 'gpt-6-sol', 'effort': 'xhigh'},
                                  user={'model': 'gpt-6-astra', 'effort': 'max'})
        self.assertFalse(result['crew_start_allowed'])
        self.assertEqual(result['model'], 'gpt-6-sol')

    def test_explicit_user_statement_when_session_unavailable(self):
        result = resolve_settings(session_reader=lambda: read_current_turn(self.home, 'missing'),
                                  user={'model': 'gpt-6-astra', 'effort': 'high'})
        self.assertTrue(result['crew_start_allowed'])
        self.assertEqual(result['source'], 'current_user_statement')

    def test_no_evidence_does_not_read_global_defaults_or_create_databases(self):
        missing = self.home / 'missing'
        missing.mkdir()
        (missing / 'config.toml').write_text('model="gpt-6-astra"\nmodel_reasoning_effort="max"\n')
        result = resolve_settings(session_reader=lambda: read_current_turn(missing, 'thread-a'))
        self.assertEqual(result['source'], 'unknown')
        self.assertFalse(result['crew_start_allowed'])
        self.assertEqual([p.name for p in missing.iterdir()], ['config.toml'])

    def test_completed_turn_is_not_current_evidence(self):
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute("UPDATE thread_turns SET status='completed'")
        self.assertEqual(resolve_settings(session_reader=self.session)['source'], 'unknown')

    def test_ambiguous_active_turns_are_not_guessed(self):
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute('INSERT INTO thread_turns VALUES (?,?,?,?)', ('thread-a', 'turn-b', 'inProgress', self.offset))
        self.assertEqual(resolve_settings(session_reader=self.session)['source'], 'unknown')

    def test_explicit_turn_mismatch_and_foreign_rollout_are_rejected(self):
        with self.assertRaises(ValueError):
            read_current_turn(self.home, 'thread-a', 'old')
        self.log.write_text(self.log.read_text().replace('"id": "thread-a"', '"id": "foreign"'))
        self.assertEqual(resolve_settings(session_reader=self.session)['source'], 'unknown')

    def test_child_context_cannot_start_coordinator(self):
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('UPDATE threads SET source=?', ('{"subagent":{}}',))
        self.assertFalse(resolve_settings(session_reader=self.session)['crew_start_allowed'])

    def test_worker_identity_blocks_explicit_runtime_and_user_overrides(self):
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('UPDATE threads SET source=?', ('{"subagent":{}}',))
        for prefix in ('runtime', 'user'):
            run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/current_session.py'),
                                  '--home', str(self.home), '--thread-id', 'thread-a',
                                  f'--{prefix}-model', 'gpt-6-astra', f'--{prefix}-effort', 'high'],
                                 capture_output=True, text=True, check=True)
            self.assertFalse(json.loads(run.stdout)['crew_start_allowed'])
            self.assertFalse(json.loads(run.stdout)['primary'])

    def test_no_offset_or_matching_context_does_not_replay_history(self):
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute('UPDATE thread_turns SET rollout_byte_offset=NULL')
        self.assertEqual(resolve_settings(session_reader=self.session)['source'], 'unknown')
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.execute('UPDATE thread_turns SET rollout_byte_offset=?', (self.offset,))
        self.log.write_bytes(self.log.read_bytes()[:self.offset])
        self.assertEqual(resolve_settings(session_reader=self.session)['source'], 'unknown')


if __name__ == '__main__':
    unittest.main()
