"""Packaging must work without touching any real user profile."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_tap_updater_rejects_mismatched_release(self):
        spec = importlib.util.spec_from_file_location("update_formula", ROOT / "packaging/update-formula.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            archive = dest / "codex-switcher-1.0.0.tar.gz"
            archive.write_bytes(b"release contents")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (dest / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
            (dest / "codex-switcher.rb").write_text(
                'url "https://github.com/forkunsafe/codex-switcher/releases/download/v1.0.0/codex-switcher-1.0.0.tar.gz"\n'
                f'sha256 "{digest}"\n')
            target = dest / "Formula/codex-switcher.rb"
            module.update(dest, "v1.0.0", target)
            before = target.read_bytes()
            archive.write_bytes(b"tampered contents")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                module.update(dest, "v1.0.0", target)
            self.assertEqual(target.read_bytes(), before)

    def test_release_is_reproducible_and_has_only_public_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            subprocess.run(["python3", str(ROOT / "scripts/build-release.py"), "--output", str(dest)], check=True, capture_output=True)
            version = (ROOT / "VERSION").read_text().strip()
            archive = dest / f"codex-switcher-{version}.tar.gz"
            first = archive.read_bytes()
            subprocess.run(["python3", str(ROOT / "scripts/build-release.py"), "--output", str(dest)], check=True, capture_output=True)
            self.assertEqual(first, archive.read_bytes())
            digest = hashlib.sha256(first).hexdigest()
            self.assertIn(digest, (dest / "SHA256SUMS").read_text())
            with tarfile.open(archive) as tar:
                names = {Path(m.name).name for m in tar.getmembers()}
                self.assertEqual(names, {"codex-cli", "codex-session-paths.sh", "install.sh", "VERSION", "LICENSE", "README.md"})
                self.assertTrue(all(m.isfile() and m.uid == 0 and m.gid == 0 for m in tar.getmembers()))
                tar.extractall(dest, filter="data")
            package = dest / f"codex-switcher-{version}"
            # Simulate the formula's libexec layout and executable shim.
            libexec = dest / "prefix/libexec"
            libexec.mkdir(parents=True)
            for name in ("codex-cli", "codex-session-paths.sh", "install.sh", "VERSION"):
                shutil.copy2(package / name, libexec / name)
            bin_dir = dest / "prefix/bin"
            bin_dir.mkdir()
            wrapper = bin_dir / "codex-cli"
            wrapper.write_text(f'#!/bin/bash\nexec "{libexec}/codex-cli" "$@"\n')
            wrapper.chmod(0o755)
            home = dest / "home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home), PATH="/usr/bin:/bin:/usr/sbin:/sbin")
            result = subprocess.run([str(wrapper), "--switcher-version"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"codex-switcher {version}")
            self.assertEqual(list(home.iterdir()), [])
            formula = dest / "codex-switcher.rb"
            subprocess.run(["python3", str(ROOT / "scripts/render-formula.py"), str(archive), "--output", str(formula)], check=True)
            self.assertIn(digest, formula.read_text())
            self.assertIn(f"/v{version}/", formula.read_text())
            self.assertNotIn("@VERSION@", formula.read_text())
