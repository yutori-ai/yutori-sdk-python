"""Tests for the shared atomic-write helper used by credentials and overlay_build."""

from __future__ import annotations

import os

import pytest

from yutori._fileutils import atomic_write_text


def test_writes_contents(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_sets_requested_mode(tmp_path):
    target = tmp_path / "secret.txt"
    atomic_write_text(target, "s3cr3t", mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_default_mode_is_owner_only(tmp_path):
    target = tmp_path / "secret.txt"
    atomic_write_text(target, "s3cr3t")
    assert (target.stat().st_mode & 0o777) == 0o600


def test_no_temp_files_left_behind(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, "hello")
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_overwrites_existing_file(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_temp_file_cleaned_up_when_write_fails(tmp_path, monkeypatch):
    target = tmp_path / "config.json"

    real_fdopen = os.fdopen

    def failing_fdopen(fd, *args, **kwargs):
        os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "hello")
    monkeypatch.setattr(os, "fdopen", real_fdopen)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
