#!/usr/bin/env python3
"""Prepare reviewable publication snapshots locally. Does not contact GitHub."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ".github/workflows/ci.yml", ".github/workflows/release.yml", ".gitignore",
    "LICENSE", "VERSION", "README.md", "codex-cli", "codex-session-paths.sh", "install.sh",
    "tests/test_switcher.py", "tests/test_packaging.py", "scripts/build-release.py",
    "scripts/render-formula.py", "scripts/stage-publication.py",
    "packaging/codex-switcher.rb.in", "packaging/tap-update.yml", "packaging/update-formula.py",
)


def copy(source, destination):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def stage(output):
    # Refuse to merge into an old staging tree: its contents must be exact.
    public = output / "github-codex-switcher"
    tap = output / "github-homebrew-tap"
    public.mkdir(parents=True, exist_ok=False)
    tap.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        copy(ROOT / name, public / name)
    subprocess.run([sys.executable, str(ROOT / "scripts/build-release.py"), "--output", str(output)], check=True)
    version = (ROOT / "VERSION").read_text().strip()
    archive = output / f"codex-switcher-{version}.tar.gz"
    formula = output / "codex-switcher.rb"
    subprocess.run([sys.executable, str(ROOT / "scripts/render-formula.py"), str(archive), "--output", str(formula)], check=True)
    copy(formula, tap / "Formula/codex-switcher.rb")
    copy(ROOT / "packaging/tap-update.yml", tap / ".github/workflows/update.yml")
    copy(ROOT / "packaging/update-formula.py", tap / "scripts/update-formula.py")
    copy(ROOT / "LICENSE", tap / "LICENSE")
    (tap / "README.md").write_text("""# forkunsafe Homebrew tap

```bash
brew install forkunsafe/tap/codex-switcher
codex-cli setup
```

Install the official Codex CLI separately if needed: `brew install --cask codex`.
Update with `brew update && brew upgrade codex-switcher`.
The program and formula use Apache License 2.0 with Commons Clause 1.0; see LICENSE.
The update workflow checks stable upstream releases daily and can be run manually.
It verifies the archive SHA256 and formula metadata before committing an update.
Only the built-in repository token is used; no personal token is required.
""")
    entries = []
    for tree in (public, tap):
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output)}")
    (output / "PUBLICATION-SHA256SUMS").write_text("\n".join(entries) + "\n")
    print(f"Local publication snapshots: {public}, {tap}")


if __name__ == "__main__":
    stage(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/publication")
