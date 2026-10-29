#!/usr/bin/env python3
"""Verify an upstream release before copying its formula into this tap."""
import hashlib
from pathlib import Path
import re
import shutil
import sys


def update(directory, tag, destination):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Expected a stable release tag")
    name = f"codex-switcher-{tag[1:]}.tar.gz"
    manifest = (directory / "SHA256SUMS").read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}  " + re.escape(name), manifest):
        raise ValueError("Unexpected checksum manifest")
    digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    if manifest.split()[0] != digest:
        raise ValueError("Archive checksum mismatch")
    source = directory / "codex-switcher.rb"
    formula = source.read_text()
    url = f'https://github.com/forkunsafe/codex-switcher/releases/download/{tag}/{name}'
    if f'url "{url}"' not in formula or f'sha256 "{digest}"' not in formula:
        raise ValueError("Formula does not match the release archive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


if __name__ == "__main__":
    update(Path(sys.argv[1]), sys.argv[2], Path("Formula/codex-switcher.rb"))
