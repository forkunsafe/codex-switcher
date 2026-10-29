#!/usr/bin/env python3
"""Build a reproducible, architecture-independent release from explicit inputs."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("codex-cli", "codex-session-paths.sh", "install.sh", "VERSION", "LICENSE", "README.md")


def build(output):
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must contain a stable semantic version")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"codex-switcher-{version}.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=9) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name in FILES:
                data = (ROOT / name).read_bytes()
                item = tarfile.TarInfo(f"codex-switcher-{version}/{name}")
                item.size = len(data)
                item.mode = 0o755 if name in ("codex-cli", "install.sh") else 0o644
                tar.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    print(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    build(parser.parse_args().output)
