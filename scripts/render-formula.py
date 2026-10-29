#!/usr/bin/env python3
"""Render the Homebrew formula from a release archive's checksum."""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive", type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
version = (ROOT / "VERSION").read_text().strip()
if args.archive.name != f"codex-switcher-{version}.tar.gz":
    parser.error("Archive filename does not match VERSION")
text = (ROOT / "packaging/codex-switcher.rb.in").read_text()
text = text.replace("@VERSION@", version).replace("@SHA256@", hashlib.sha256(args.archive.read_bytes()).hexdigest())
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(text)
