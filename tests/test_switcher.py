"""Integration checks with isolated homes; no real accounts or Codex processes."""
import json
import os
import pty
import select
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SwitcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-switcher-test-")
        self.base = Path(self.temp.name)
        self.home = self.base / 'home with both \' and " quotes %?#'
        self.project = self.base / "project"
        self.bin = self.base / "bin"
        for p in (self.home, self.project, self.bin):
            p.mkdir()
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin")
        self.stub("codex", '''#!/usr/bin/python3
import json, os, sys
print(json.dumps({'home': os.environ['CODEX_HOME'], 'args': sys.argv[1:]}))
''')
        self.stub("lsof", "#!/bin/bash\nexit 1\n")
        self.stub("pgrep", "#!/bin/bash\nexit 1\n")
        self.stub("osascript", "#!/bin/bash\nprintf 'false\\n'\n")
        for name in ("personal", "work"):
            p = self.home / f".codex-{name}"
            p.mkdir()
            (p / "auth.json").write_text("{}")
        (self.project / ".codex-account").write_text("work\n")
        (self.home / ".codex").symlink_to(self.home / ".codex-work")

    def tearDown(self):
        self.temp.cleanup()

    def stub(self, name, text):
        p = self.bin / name
        p.write_text(text)
        p.chmod(0o755)

    def run_wrapper(self, *args, installed=False, input_text=None):
        wrapper = self.home / ".local/bin/codex-cli" if installed else ROOT / "codex-cli"
        return subprocess.run(["/bin/bash", str(wrapper), *args], cwd=self.project,
                              env=self.env, input=input_text, text=True, capture_output=True)

    def seed(self, profile="personal", broken=False):
        home = self.home / f".codex-{profile}"
        db = home / "state_5.sqlite"
        rows = []
        for ident, folder in (("session", "sessions"), ("archive", "archived_sessions")):
            file = home / folder / "2026" / f"{ident}.jsonl"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{"type":"session_meta"}\n')
            rows.append((ident, str(self.home / ".codex" / folder / "2026" / file.name)))
        rows += [("missing", str(self.home / ".codex/sessions/missing.jsonl")),
                 ("foreign", "/another/profile/sessions/file.jsonl")]
        with sqlite3.connect(db) as c:
            c.execute("create table threads (id text primary key, rollout_path text)")
            c.executemany("insert into threads values (?, ?)", rows)
            if broken:
                c.execute("create trigger fail before update on threads when new.id='archive' "
                          "begin select raise(abort, 'test failure'); end")
        return db, dict(rows)

    def interactive_launch(self, args, expect_pause):
        master, slave = pty.openpty()
        p = subprocess.Popen(["/bin/bash", str(ROOT / "codex-cli"), *args],
                             cwd=self.project, env=self.env,
                             stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b""
        deadline = time.monotonic() + 8
        released = not expect_pause
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], .1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                if not released and b"Press Enter to start Codex" in output:
                    self.assertIsNone(p.poll())
                    self.assertNotIn(b'"args":', output)
                    os.write(master, b"\n")
                    released = True
            self.assertTrue(released, output.decode())
            self.assertEqual(p.wait(timeout=2), 0, output.decode())
            return output.decode()
        finally:
            os.close(master)
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=2)

    def rows(self, db):
        with sqlite3.connect(db) as c:
            return dict(c.execute("select id, rollout_path from threads"))

    def test_override_preserves_marker_and_forwards_arguments(self):
        p = self.run_wrapper("--use", "personal", "resume", "session-id", "--all")
        self.assertEqual(p.returncode, 0, p.stderr)
        launch = json.loads(p.stdout)
        self.assertEqual(launch["home"], str(self.home / ".codex-personal"))
        self.assertEqual(launch["args"], ["--no-daemon", "resume", "session-id", "--all"])
        self.assertEqual((self.project / ".codex-account").read_text(), "work\n")
        self.assertEqual((self.home / ".codex").resolve(), (self.home / ".codex-work").resolve())
        (self.project / ".codex-account").unlink()
        p = self.run_wrapper("--use", "work", "exec", "argument with spaces")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.project / ".codex-account").exists())

    def test_invalid_override(self):
        for args in [("--use",), ("--use", "typo"), ("--use", "work", "--switch")]:
            self.assertEqual(self.run_wrapper(*args).returncode, 2)

    def test_override_leaves_git_exclusions_and_invalid_marker_alone(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.env)
        exclude = self.project / ".git/info/exclude"
        original = exclude.read_bytes()
        (self.project / ".codex-account").write_text("invalid marker\n")
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(exclude.read_bytes(), original)
        self.assertEqual((self.project / ".codex-account").read_text(), "invalid marker\n")

    def test_default_launch_keeps_project_account(self):
        p = self.run_wrapper("exec", "fix tests")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["home"], str(self.home / ".codex-work"))
        self.assertNotIn("Press Enter", p.stderr)

    def test_interactive_startup_hint_waits_for_enter(self):
        output = self.interactive_launch([], expect_pause=True)
        self.assertIn("/resume opens chats from the work account only", output)
        self.assertIn("codex-cli --use personal", output)
        self.assertIn('"args": ["--no-daemon"]', output)

    def test_interactive_override_hint_uses_selected_account(self):
        output = self.interactive_launch(["--use", "personal"], expect_pause=True)
        self.assertIn("/resume opens chats from the personal account only", output)
        self.assertIn("codex-cli --use work", output)
        self.assertEqual((self.project / ".codex-account").read_text(), "work\n")

    def test_explicit_interactive_command_does_not_pause(self):
        output = self.interactive_launch(["resume"], expect_pause=False)
        self.assertNotIn("Press Enter", output)
        self.assertIn('"args": ["--no-daemon", "resume"]', output)

    def test_repair_backup_and_idempotency(self):
        db, original = self.seed()
        work_db, work_original = self.seed("work")
        p = self.run_wrapper("--use", "personal", "resume", "session")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = self.rows(db)
        self.assertTrue(Path(rows["session"]).is_file())
        self.assertTrue(Path(rows["archive"]).is_file())
        self.assertEqual(rows["missing"], original["missing"])
        self.assertEqual(rows["foreign"], original["foreign"])
        self.assertEqual(self.rows(work_db), work_original)
        backups = list(db.parent.glob("*.before-path-migration.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.rows(backups[0]), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_wrapper("--use", "personal", "resume").returncode, 0)
        self.assertEqual(list(db.parent.glob("*.before-path-migration.*")), backups)

    def test_unopenable_closed_database_uses_immutable_fallback(self):
        db, original = self.seed()
        self.stub("sqlite3", '''#!/bin/bash
if [ "$1" = "-readonly" ]; then exit 1; fi
exec /usr/bin/sqlite3 "$@"
''')
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotEqual(self.rows(db), original)
        self.assertTrue(Path(self.rows(db)["session"]).is_file())
        self.assertNotIn("Skipping", p.stderr)

    def test_unreadable_database_does_not_block_launch(self):
        db = self.home / ".codex-work/state_5.sqlite"
        db.write_bytes(b"not a SQLite database")
        p = self.run_wrapper("exec", "fix tests")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Skipping automatic repair", p.stderr)
        self.assertEqual(json.loads(p.stdout)["args"], ["--no-daemon", "exec", "fix tests"])
        self.assertEqual(db.read_bytes(), b"not a SQLite database")

    def test_immutable_fallback_never_ignores_live_database(self):
        db, original = self.seed()
        self.stub("sqlite3", '''#!/bin/bash
if [ "$1" = "-readonly" ]; then exit 1; fi
echo unexpected-fallback > "$HOME/fallback-used"
exit 1
''')
        self.stub("lsof", "#!/bin/bash\necho 1234\n")
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.home / "fallback-used").exists())
        self.assertNotIn("Choose", p.stderr)
        self.assertEqual(self.rows(db), original)

    def test_immutable_fallback_never_ignores_wal(self):
        db, original = self.seed()
        Path(str(db) + "-wal").write_bytes(b"pending WAL data")
        self.stub("sqlite3", '''#!/bin/bash
if [ "$1" = "-readonly" ]; then exit 1; fi
echo unexpected-fallback > "$HOME/fallback-used"
exit 1
''')
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.home / "fallback-used").exists())
        # Do not query the database here: SQLite might consume the test WAL.
        self.assertEqual(Path(str(db) + "-wal").read_bytes(), b"pending WAL data")

    def test_busy_database_does_not_launch_or_mutate(self):
        db, original = self.seed()
        self.stub("lsof", "#!/bin/bash\nprintf '1234\\n'\n")
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("is in use", p.stderr)
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.rows(db), original)
        self.assertFalse(list(db.parent.glob("*.before-path-migration.*")))

    def test_automatic_closure_migrates_then_launches(self):
        db, original = self.seed()
        marker = self.base / "holder-running"
        worker = subprocess.Popen(["/usr/bin/python3", "-c", '''
import pathlib, signal, sys, time
p = pathlib.Path(sys.argv[1])
def stop(*args):
    p.unlink()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
p.write_text('ready')
while True: time.sleep(.1)
''', str(marker)])
        try:
            import time
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(.01)
            self.assertTrue(marker.exists())
            self.env.update(TEST_HOLDER_PID=str(worker.pid), TEST_HOLDER_MARKER=str(marker))
            self.stub("lsof", '#!/bin/bash\n[ -f "$TEST_HOLDER_MARKER" ] || exit 1\nprintf "%s\\n" "$TEST_HOLDER_PID"\n')
            self.stub("ps", '#!/bin/bash\ncase "$*" in *ppid=*) echo 1 ;; *) echo test-worker ;; esac\n')
            p = self.run_wrapper("--use", "personal", "resume", input_text="1\n")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(worker.wait(timeout=3), 0)
            self.assertNotEqual(self.rows(db), original)
            self.assertEqual(json.loads(p.stdout)["args"], ["--no-daemon", "resume"])
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=3)

    def test_manual_closure_preserves_database(self):
        db, original = self.seed()
        self.stub("lsof", "#!/bin/bash\nprintf '1234\\n'\n")
        p = self.run_wrapper("--use", "personal", "resume", input_text="2\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("rerun the same command", p.stderr)
        self.assertEqual(self.rows(db), original)
        self.assertEqual(p.stdout, "")

    def test_unresponsive_process_blocks_migration_without_force_kill(self):
        db, original = self.seed()
        marker = self.base / "unresponsive-holder"
        worker = subprocess.Popen(["/usr/bin/python3", "-c", '''
import pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text('ready')
while True: time.sleep(.1)
''', str(marker)])
        try:
            import time
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(.01)
            self.assertTrue(marker.exists())
            self.env.update(TEST_HOLDER_PID=str(worker.pid), TEST_HOLDER_MARKER=str(marker))
            self.stub("lsof", '#!/bin/bash\n[ -f "$TEST_HOLDER_MARKER" ] || exit 1\nprintf "%s\\n" "$TEST_HOLDER_PID"\n')
            self.stub("ps", '#!/bin/bash\ncase "$*" in *ppid=*) echo 1 ;; *) echo test-worker ;; esac\n')
            self.stub("sleep", "#!/bin/bash\nexit 0\n")
            p = self.run_wrapper("--use", "personal", "resume", input_text="1\n")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("still in use", p.stderr)
            self.assertEqual(self.rows(db), original)
            self.assertEqual(p.stdout, "")
            self.assertIsNone(worker.poll())
        finally:
            worker.kill()
            worker.wait(timeout=3)

    def test_ancestor_is_never_terminated(self):
        db, original = self.seed()
        self.env["TEST_HOLDER_PID"] = str(os.getpid())
        self.stub("lsof", '#!/bin/bash\nprintf "%s\\n" "$TEST_HOLDER_PID"\n')
        self.stub("ps", '#!/bin/bash\ncase "$*" in *ppid=*) echo "$TEST_HOLDER_PID" ;; *) echo test-worker ;; esac\n')
        p = self.run_wrapper("--use", "personal", "resume", input_text="1\n")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("separate terminal", p.stderr)
        self.assertEqual(self.rows(db), original)

    def test_no_process_inspection_after_repair(self):
        self.seed()
        self.assertEqual(self.run_wrapper("--use", "personal", "resume").returncode, 0)
        self.stub("lsof", '#!/bin/bash\necho inspected > "$HOME/unexpected-inspection"\nexit 1\n')
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.home / "unexpected-inspection").exists())
        self.assertNotIn("Choose", p.stderr)

    def test_sql_failure_rolls_back_all_updates(self):
        db, original = self.seed(broken=True)
        p = self.run_wrapper("--use", "personal", "resume")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.rows(db), original)
        self.assertEqual(p.stdout, "")

    def test_installer_and_installed_wrapper(self):
        db, original = self.seed()
        p = subprocess.run(["/bin/bash", str(ROOT / "install.sh")], cwd=self.project,
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotEqual(self.rows(db), original)
        self.assertTrue((self.home / ".local/bin/codex-switcher-support/codex-session-paths.sh").is_file())
        p = self.run_wrapper("--use", "personal", "resume", installed=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["args"], ["--no-daemon", "resume"])

    def test_wrapper_only_update_defers_migration_and_preserves_backup(self):
        db, original = self.seed()
        dest = self.home / ".local/bin"
        dest.mkdir(parents=True)
        (dest / "codex-cli").write_text("old wrapper")
        p = subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--wrapper-only"],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.rows(db), original)
        self.assertEqual((dest / "codex-cli.bak").read_text(), "old wrapper")
        self.assertFalse((self.home / ".bashrc").exists())
        self.assertFalse((db.parent / "config.toml").exists())

    def test_setup_does_not_install_wrapper_or_edit_shell_files(self):
        p = self.run_wrapper("setup")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((self.home / ".local/bin/codex-cli").exists())
        self.assertFalse((self.home / ".bashrc").exists())
        self.assertFalse((self.home / ".bash_profile").exists())
        for account in ("personal", "work"):
            config = (self.home / f".codex-{account}/config.toml").read_text()
            self.assertIn('cli_auth_credentials_store = "file"', config)
        self.assertEqual((self.project / ".codex-account").read_text(), "work\n")

    def test_installed_setup_and_version_find_support_files(self):
        p = subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--wrapper-only"],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_wrapper("--switcher-version", installed=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "codex-switcher " + (ROOT / "VERSION").read_text().strip())
        p = self.run_wrapper("setup", installed=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_initial_directory_migration_rewrites_index(self):
        db, original = self.seed()
        legacy = self.home / ".codex"
        legacy.unlink()
        db.parent.rename(legacy)
        p = subprocess.run(["/bin/bash", str(ROOT / "install.sh")], cwd=self.project,
                           env=self.env, input="personal\n", text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(legacy.is_symlink())
        self.assertTrue(Path(self.rows(db)["session"]).is_file())
        self.assertNotEqual(self.rows(db)["session"], original["session"])


if __name__ == "__main__":
    unittest.main()
