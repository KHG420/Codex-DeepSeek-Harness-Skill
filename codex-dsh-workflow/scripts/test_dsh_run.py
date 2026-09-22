"""Offline behavioral checks for evidence and failure handling; never calls a model."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = Path(__file__).with_name("dsh_run.py")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-dsh-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace with spaces"
        self.workspace.mkdir()
        self.task = self.root / "task.md"
        self.task.write_text('Task containing literal $(touch UNEXPECTED) and `touch UNEXPECTED2`.\n')
        (self.workspace / "value.py").write_text("VALUE = 1\n")
        self.git("init", "-b", "codex/test")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "fixture")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.fake = bin_dir / "dsh"
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
        self.fake_dsh("print('Implementation completed; Codex must review.')")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.workspace), *args], stderr=subprocess.DEVNULL)

    def fake_dsh(self, body):
        self.fake.write_text(f"#!{sys.executable}\nimport os, pathlib, subprocess, sys, time\n{body}\n")
        self.fake.chmod(0o755)

    def args(self, attempt="attempt-01", *extra):
        return [sys.executable, str(RUNNER), "run", "--workspace", str(self.workspace),
                "--task", str(self.task), "--output", str(self.root / attempt), "--capture", "value.py", *extra]

    def run_task(self, attempt="attempt-01", *extra):
        return subprocess.run(self.args(attempt, *extra), env=self.env, capture_output=True, text=True)

    def check(self, attempt="attempt-01"):
        return subprocess.run([sys.executable, str(RUNNER), "check", "--output", str(self.root / attempt)],
                              capture_output=True, text=True)

    def result(self, attempt="attempt-01"):
        return json.loads((self.root / attempt / "result.json").read_text())

    def test_success_records_candidate_not_acceptance_and_uses_literal_args(self):
        self.fake_dsh("assert sys.argv[1:3] == ['--profile', 'headless']\n"
                      "assert '$(touch UNEXPECTED)' in sys.argv[3]\n"
                      "assert pathlib.Path('value.py').exists()\n"
                      "pathlib.Path('value.py').write_text('VALUE = 2\\n')\nprint('Tests still required')")
        completed = self.run_task()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.result()["status"], "EXECUTED")
        self.assertNotIn("PASS", self.result().values())
        self.assertEqual(self.check().returncode, 0)
        self.assertFalse((self.workspace / "UNEXPECTED").exists())
        self.assertFalse((self.workspace / "UNEXPECTED2").exists())
        self.assertIn("VALUE = 2", (self.root / "attempt-01/changes.patch").read_text())

    def test_nonzero_exit_keeps_logs_and_candidate(self):
        self.fake_dsh("print('provider unavailable', file=sys.stderr)\nsys.exit(7)")
        self.assertEqual(self.run_task().returncode, 7)
        self.assertEqual(self.result()["status"], "EXECUTION_FAILED")
        self.assertIn("provider unavailable", (self.root / "attempt-01/stderr.log").read_text())
        self.assertEqual(json.loads(self.check().stdout)["execution_status"], "EXECUTION_FAILED")

    def test_incremental_diff_uses_dirty_baseline_and_new_files(self):
        (self.workspace / "value.py").write_text("VALUE = 8\n")
        (self.workspace / "untouched.py").write_text("USER_WORK = 1\n")
        self.fake_dsh("pathlib.Path('value.py').write_text('VALUE = 9\\n')\n"
                      "pathlib.Path('new.py').write_text('NEW = 2\\n')")
        done = self.run_task("attempt-01", "--capture", "value.py", "--capture", "new.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        patch = (self.root / "attempt-01/changes.patch").read_text()
        self.assertIn("-VALUE = 8", patch)
        self.assertNotIn("-VALUE = 1", patch)
        self.assertIn("+NEW = 2", patch)
        self.assertNotIn("USER_WORK", patch)

    def test_external_acceptance_input_drift_is_rejected(self):
        fixture = self.root / "oracle.txt"
        fixture.write_text("expected: 170")
        done = self.run_task("attempt-01", "--input", str(fixture))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.check().returncode, 0)
        fixture.write_text("expected: 190")
        self.assertNotEqual(self.check().returncode, 0)

    def test_input_changed_during_execution_fails_evidence(self):
        fixture = self.root / "oracle.txt"
        fixture.write_text("frozen")
        self.fake_dsh(f"pathlib.Path({str(fixture)!r}).write_text('changed')")
        self.assertNotEqual(self.run_task("attempt-01", "--input", str(fixture)).returncode, 0)
        self.assertEqual(self.result()["status"], "EVIDENCE_FAILED")

    def test_frozen_copy_and_baseline_blob_tampering_are_rejected(self):
        fixture = self.root / "oracle.txt"
        fixture.write_text("frozen")
        self.assertEqual(self.run_task("attempt-01", "--input", str(fixture)).returncode, 0)
        frozen = self.root / "attempt-01" / self.result()["inputs"][0]["copy"]
        frozen.write_text("tampered")
        self.assertNotEqual(self.check().returncode, 0)
        frozen.write_text("frozen")
        sha = self.result()["captured_before"]["value.py"]
        (self.root / "attempt-01/blobs" / sha).write_text("bad snapshot")
        self.assertNotEqual(self.check().returncode, 0)

    def test_scoped_diff_deletion_binary_mode_and_symlink(self):
        source = self.workspace / "src"
        source.mkdir()
        (source / "deleted.txt").write_text("old untracked data\n")
        (source / "binary.bin").write_bytes(b"\x00old")
        (source / "mode.sh").write_text("echo test\n")
        secret = self.root / "secret"
        secret.write_text("DO_NOT_CAPTURE_TARGET")
        (source / "link").symlink_to(secret)
        self.fake_dsh("pathlib.Path('src/deleted.txt').unlink()\n"
                      "pathlib.Path('src/binary.bin').write_bytes(b'\\x00new')\n"
                      "pathlib.Path('src/mode.sh').chmod(0o755)\n"
                      "pathlib.Path('src/link').unlink()\n"
                      "pathlib.Path('src/link').symlink_to('other-target')")
        done = self.run_task("attempt-01", "--capture", "src")
        self.assertEqual(done.returncode, 0, done.stderr)
        patch = (self.root / "attempt-01/changes.patch").read_text()
        self.assertIn("-old untracked data", patch)
        self.assertIn("Binary content", patch)
        delta = json.loads((self.root / "attempt-01/delta.json").read_text())
        mode = next(d for d in delta if d["path"] == "src/mode.sh")
        self.assertNotEqual(mode["before"]["mode"], mode["after"]["mode"])
        for blob in (self.root / "attempt-01/blobs").iterdir():
            self.assertNotIn(b"DO_NOT_CAPTURE_TARGET", blob.read_bytes())
        self.assertEqual(self.check().returncode, 0)

    def test_out_of_scope_change_is_visible_without_copying_contents(self):
        self.fake_dsh("pathlib.Path('.env').write_text('PASSWORD=PRIVATE')")
        self.assertEqual(self.run_task().returncode, 0)
        delta = json.loads((self.root / "attempt-01/delta.json").read_text())
        self.assertFalse(next(d for d in delta if d["path"] == ".env")["content_captured"])
        self.assertNotIn("PASSWORD", (self.root / "attempt-01/changes.patch").read_text())
        for blob in (self.root / "attempt-01/blobs").iterdir():
            self.assertNotIn(b"PASSWORD", blob.read_bytes())
        self.assertEqual((self.root / "attempt-01").stat().st_mode & 0o777, 0o700)

    def test_root_and_oversized_capture_rejected_before_execution(self):
        self.fake_dsh("pathlib.Path('MODEL_RAN').touch()")
        for name in (".", "..", "/", ".git"):
            with self.subTest(name=name):
                self.assertNotEqual(self.run_task("rejected", "--capture", name).returncode, 0)
        (self.workspace / "huge").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        self.assertNotEqual(self.run_task("oversized", "--capture", "huge").returncode, 0)
        self.assertFalse((self.workspace / "MODEL_RAN").exists())

    def test_missing_new_capture_target_and_empty_new_file_are_recorded(self):
        self.fake_dsh("pathlib.Path('future').mkdir()\npathlib.Path('future/empty').touch()")
        done = self.run_task("attempt-01", "--capture", "future")
        self.assertEqual(done.returncode, 0, done.stderr)
        delta = json.loads((self.root / "attempt-01/delta.json").read_text())
        created = next(item for item in delta if item["path"] == "future/empty")
        self.assertIsNone(created["before"])
        self.assertTrue(created["content_captured"])
        self.assertEqual(self.check().returncode, 0)

    def test_staged_user_work_is_the_baseline_not_head(self):
        (self.workspace / "value.py").write_text("VALUE = 4\n")
        self.git("add", "value.py")
        self.fake_dsh("pathlib.Path('value.py').write_text('VALUE = 5\\n')")
        self.assertEqual(self.run_task().returncode, 0)
        patch = (self.root / "attempt-01/changes.patch").read_text()
        self.assertIn("-VALUE = 4", patch)
        self.assertNotIn("-VALUE = 1", patch)
        self.assertEqual(self.git("show", ":value.py"), b"VALUE = 4\n")

    def test_symlink_acceptance_file_is_rejected(self):
        source = self.root / "source"
        source.write_text("data")
        link = self.root / "linked"
        link.symlink_to(source)
        self.assertNotEqual(self.run_task("attempt-01", "--input", str(link)).returncode, 0)

    def test_record_rejects_wrong_workspace_candidate(self):
        self.assertEqual(self.run_task().returncode, 0)
        result = self.result()
        result["workspace"] = "/another/workspace"
        (self.root / "attempt-01/result.json").write_text(json.dumps(result))
        done = self.record("verification", "print('should not run')", "--candidate", str(self.root / "attempt-01"))
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse((self.root / "verification").exists())

    def record(self, name, code, *extra):
        return subprocess.run([sys.executable, str(RUNNER), "record", "--workspace", str(self.workspace),
                               "--output", str(self.root / name), *extra, "--", sys.executable, "-c", code],
                              capture_output=True, text=True)

    def test_record_actual_exit_logs_and_literal_arguments(self):
        code = "import sys; print('$(touch UNEXPECTED)'); print('failure detail', file=sys.stderr); sys.exit(7)"
        done = self.record("verification", code)
        self.assertEqual(done.returncode, 7, done.stderr)
        result = json.loads((self.root / "verification/result.json").read_text())
        self.assertEqual(result["argv"][-1], code)
        self.assertEqual(result["status"], "COMMAND_FAILED")
        self.assertFalse(result["workspace_changed"])
        self.assertIn("failure detail", (self.root / "verification/stderr.log").read_text())
        self.assertFalse((self.workspace / "UNEXPECTED").exists())

    def test_record_candidate_detects_command_rewriting_code(self):
        self.assertEqual(self.run_task().returncode, 0)
        done = self.record("verification", "from pathlib import Path; Path('value.py').write_text('changed')",
                           "--candidate", str(self.root / "attempt-01"))
        self.assertNotEqual(done.returncode, 0)
        result = json.loads((self.root / "verification/result.json").read_text())
        self.assertEqual(result["status"], "EVIDENCE_FAILED")
        self.assertEqual(result["command_exit_code"], 0)
        self.assertTrue(result["workspace_changed"])

    def test_record_candidate_success_and_duplicate_protection(self):
        self.assertEqual(self.run_task().returncode, 0)
        done = self.record("verification", "print('verified')", "--candidate", str(self.root / "attempt-01"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotEqual(self.record("verification", "print('overwrite')").returncode, 0)
        self.assertEqual((self.root / "verification/stdout.log").read_text(), "verified\n")

    def test_record_timeout_and_launch_failure(self):
        done = self.record("timeout", "import time; time.sleep(10)", "--timeout", "1")
        self.assertEqual(done.returncode, 124, done.stderr)
        done = subprocess.run([sys.executable, str(RUNNER), "record", "--workspace", str(self.workspace),
                               "--output", str(self.root / "launch"), "--", "/nonexistent/dsh-test-command"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads((self.root / "launch/result.json").read_text())["status"], "LAUNCH_FAILED")

    def test_timeout_terminates_shell_descendants_and_records_failure(self):
        self.fake_dsh("subprocess.Popen([sys.executable, '-c', \"import pathlib,time; time.sleep(3); pathlib.Path('late-write').touch()\"])\ntime.sleep(30)")
        completed = self.run_task("attempt-01", "--timeout", "1")
        self.assertEqual(completed.returncode, 124, completed.stdout + completed.stderr)
        self.assertEqual(self.result()["status"], "TIMED_OUT")
        time.sleep(2)
        self.assertFalse((self.workspace / "late-write").exists())

    def test_modified_and_new_files_invalidate_candidate(self):
        self.assertEqual(self.run_task().returncode, 0)
        original = (self.workspace / "value.py").read_bytes()
        (self.workspace / "value.py").write_text("VALUE = 9\n")
        self.assertNotEqual(self.check().returncode, 0)
        (self.workspace / "value.py").write_bytes(original)
        self.assertEqual(self.check().returncode, 0)
        (self.workspace / "new.py").write_text("NEW = True\n")
        self.assertNotEqual(self.check().returncode, 0)

    def test_task_drift_is_rejected(self):
        self.assertEqual(self.run_task().returncode, 0)
        self.task.write_text("A different task")
        self.assertNotEqual(self.check().returncode, 0)

    def test_duplicate_output_is_not_overwritten(self):
        self.assertEqual(self.run_task().returncode, 0)
        original = (self.root / "attempt-01/result.json").read_bytes()
        self.assertNotEqual(self.run_task().returncode, 0)
        self.assertEqual((self.root / "attempt-01/result.json").read_bytes(), original)

    def test_rework_passes_feedback_with_original_contract(self):
        feedback = self.root / "review.md"
        feedback.write_text("R1: Fix conversion and rerun the existing regression test.")
        self.fake_dsh("assert 'R1: Fix conversion' in sys.argv[3]\nassert 'TASK CONTRACT' in sys.argv[3]\nprint('Rework executed')")
        self.assertEqual(self.run_task("attempt-02", "--feedback", str(feedback)).returncode, 0)
        self.assertIsNotNone(self.result("attempt-02")["feedback_sha256"])
        self.assertEqual(self.check("attempt-02").returncode, 0)

    def test_concurrent_writer_is_rejected(self):
        self.fake_dsh("pathlib.Path('ready').touch()\ntime.sleep(2)")
        process = subprocess.Popen(self.args(), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (self.workspace / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((self.workspace / "ready").exists())
            other = self.run_task("attempt-02")
            self.assertNotEqual(other.returncode, 0)
            self.assertIn("Another dsh runner", other.stderr)
            self.assertFalse((self.root / "attempt-02").exists())
            verification = self.record("concurrent-check", "print('must not run')")
            self.assertNotEqual(verification.returncode, 0)
            self.assertFalse((self.root / "concurrent-check").exists())
        finally:
            process.wait(timeout=10)

    def test_sigterm_stops_execution_and_records_interruption(self):
        self.fake_dsh("pathlib.Path('ready').touch()\ntime.sleep(30)")
        process = subprocess.Popen(self.args(), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (self.workspace / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((self.workspace / "ready").exists())
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 130)
            self.assertEqual(self.result()["status"], "INTERRUPTED")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
