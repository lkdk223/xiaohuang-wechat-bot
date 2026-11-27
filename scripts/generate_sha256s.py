from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_sha256s(release_dir: Path) -> Path:
    root = release_dir.expanduser().resolve()
    files = sorted(root.glob("*.exe"), key=lambda item: item.name.lower())
    if not files:
        raise FileNotFoundError(f"No release executables found in {root}")
    output = root / "SHA256SUMS.txt"
    lines = [f"{sha256_file(item)}  {item.name}" for item in files]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SHA-256 checksums for release executables.")
    parser.add_argument("release_dir", type=Path)
    args = parser.parse_args()
    output = generate_sha256s(args.release_dir)
    print(f"SHA256 checksums written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
