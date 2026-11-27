from __future__ import annotations

import hashlib

import pytest

from scripts.generate_sha256s import generate_sha256s


def test_generate_sha256s_writes_sorted_executables_only(tmp_path):
    (tmp_path / "z.exe").write_bytes(b"z")
    (tmp_path / "A.exe").write_bytes(b"a")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")

    output = generate_sha256s(tmp_path)

    assert output.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'a').hexdigest()}  A.exe",
        f"{hashlib.sha256(b'z').hexdigest()}  z.exe",
    ]


def test_generate_sha256s_rejects_empty_release_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        generate_sha256s(tmp_path)
