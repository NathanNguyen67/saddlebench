"""No-replace fallback when Lustre rejects renameat2(RENAME_NOREPLACE)."""
from __future__ import annotations
import errno
import os
from pathlib import Path
import pytest

from saddlebench.reference import safe_output


def _reject_rename(*args):
    raise OSError(errno.EINVAL, 'Lustre does not accept RENAME_NOREPLACE')


def test_einval_fallback_publishes_valid_manifest_last(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    target = tmp_path/'new_reference'
    with safe_output.AtomicDirectory(target) as writer:
        writer.write_bytes('manifest.json', b'{"ok":true}\n')
        writer.write_bytes('reference.jsonl', b'{"id":"x"}\n')
        assert writer.commit() == target
    assert (target/'manifest.json').read_bytes() == b'{"ok":true}\n'
    assert (target/'reference.jsonl').read_bytes() == b'{"id":"x"}\n'
    assert not list(tmp_path.glob('.sbe-*'))


def test_einval_does_not_replace_existing_dir(tmp_path, monkeypatch):
    target = tmp_path/'new_reference'
    protected = target/'precious.txt'
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    def race(*args):
        target.mkdir()
        protected.write_text('keep')
        _reject_rename(*args)
    monkeypatch.setattr(safe_output, '_rename_noreplace', race)
    with pytest.raises(FileExistsError):
        with safe_output.AtomicDirectory(target) as writer:
            writer.write_bytes('manifest.json', b'{}\n')
            writer.commit()
    assert protected.read_text() == 'keep'
    assert not (target/'manifest.json').exists()


def test_lustre_no_hardlinks_still_publishes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    def deny_link(*args, **kwargs):
        raise PermissionError(errno.EPERM, 'Lustre EPERM on os.link')
    monkeypatch.setattr(safe_output.os, 'link', deny_link)
    target = tmp_path / 'published'
    with safe_output.AtomicDirectory(target) as writer:
        writer.write_bytes('manifest.json', b'{"ready":true}\n')
        writer.write_bytes('reference.jsonl', b'{"id":"x"}\n')
        writer.commit()
    assert (target/'manifest.json').read_bytes() == b'{"ready":true}\n'
    assert (target/'manifest.json').is_symlink()
    assert (target/'reference.jsonl').read_bytes() == b'{"id":"x"}\n'
    assert not list(tmp_path.glob('.sbe-*'))


def test_copy_failure_never_publishes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    real_copy = safe_output.shutil.copyfileobj
    def fail_copy(source, target, length=0):
        target.write(b'partial')
        raise OSError(errno.EIO, 'simulated Lustre copy failure')
    monkeypatch.setattr(safe_output.shutil, 'copyfileobj', fail_copy)
    target = tmp_path / 'incomplete'
    with pytest.raises(OSError, match='simulated Lustre copy failure'):
        with safe_output.AtomicDirectory(target) as writer:
            writer.write_bytes('reference.jsonl', b'reference data')
            writer.write_bytes('manifest.json', b'{}\n')
            writer.commit()
    assert not (target/'manifest.json').exists()
    assert target.is_dir() and (target/'reference.jsonl').exists()


def test_atomic_manifest_never_exposes_partial_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    target = tmp_path/'reference'
    original_symlink = safe_output.os.symlink
    def inspect_then_symlink(src, dst, **kwargs):
        assert dst == 'manifest.json'
        # Manifest name must be absent until this atomic publication call.
        assert not (target/'manifest.json').exists()
        assert (target/src).read_bytes() == b'{"ready":true}\n'
        assert (target/'reference.jsonl').read_bytes() == b'hello\n'
        return original_symlink(src, dst, **kwargs)
    monkeypatch.setattr(safe_output.os, 'symlink', inspect_then_symlink)
    with safe_output.AtomicDirectory(target) as writer:
        writer.write_bytes('manifest.json', b'{"ready":true}\n')
        writer.write_bytes('reference.jsonl', b'hello\n')
        writer.commit()
    assert (target/'manifest.json').read_bytes() == b'{"ready":true}\n'

def test_retarget_parent_fails_without_writing_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    producer = tmp_path/'producer'
    producer.mkdir()
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    target = workspace/'reference'
    with pytest.raises((ValueError, FileNotFoundError)):
        with safe_output.AtomicDirectory(target, protected_roots=(producer,)) as writer:
            writer.write_bytes('manifest.json', b'{}\n')
            workspace.rename(tmp_path/'workspace_moved')
            workspace.symlink_to(producer, target_is_directory=True)
            writer.commit()
    assert not (producer/'reference').exists()


def test_no_hardlink_dependency_or_overwrite_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    def deny_link(*args, **kwargs):
        raise OSError(errno.EPERM, 'hardlinks denied')
    def deny_rename(*args, **kwargs):
        raise AssertionError('unsafe rename must not be used')
    monkeypatch.setattr(safe_output.os, 'link', deny_link)
    monkeypatch.setattr(safe_output.os, 'rename', deny_rename)
    target=tmp_path/'lustre-compatible'
    with safe_output.AtomicDirectory(target) as writer:
        writer.write_bytes('manifest.json', b'{}\n')
        writer.commit()
    assert (target/'manifest.json').read_bytes() == b'{}\n'


def test_manifest_name_race_cannot_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    target=tmp_path/'race'
    real_symlink=safe_output.os.symlink
    def racing_symlink(src,dst,**kwargs):
        (target/dst).write_text('PREEXISTING')
        return real_symlink(src,dst,**kwargs)
    monkeypatch.setattr(safe_output.os,'symlink',racing_symlink)
    with pytest.raises(FileExistsError):
        with safe_output.AtomicDirectory(target) as writer:
            writer.write_bytes('manifest.json', b'{"ready":true}\n')
            writer.commit()
    assert (target/'manifest.json').read_text() == 'PREEXISTING'
    assert not list(tmp_path.glob('.sbe-*'))


def test_data_file_race_never_replaces_protected_file(tmp_path, monkeypatch):
    """An injected destination symlink cannot redirect fd-relative O_EXCL."""
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    protected = tmp_path / 'production'
    protected.mkdir()
    untouched = protected / 'registry.sqlite'
    untouched.write_bytes(b'PROTECTED-CONTENTS')
    target = tmp_path / 'output'
    original_open = safe_output.os.open

    def race_at_open(name, flags, *args, **kwargs):
        if name == 'reference.jsonl' and target.is_dir() and (flags & os.O_CREAT):
            # A hostile process pre-populates this destination filename.
            (target/'reference.jsonl').symlink_to(untouched)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(safe_output.os, 'open', race_at_open)
    with pytest.raises(FileExistsError):
        with safe_output.AtomicDirectory(target, protected_roots=[protected]) as writer:
            writer.write_bytes('reference.jsonl', b'new contents')
            writer.write_bytes('manifest.json', b'{}\n')
            writer.commit()
    assert untouched.read_bytes() == b'PROTECTED-CONTENTS'
    assert not (target/'manifest.json').exists()


def test_manifest_publication_denied_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_output, '_rename_noreplace', _reject_rename)
    def reject_marker(*args, **kwargs):
        raise PermissionError(errno.EPERM, 'simulated symlink unsupported')
    monkeypatch.setattr(safe_output.os, 'symlink', reject_marker)
    target = tmp_path/'incomplete'
    with pytest.raises(PermissionError, match='symlink unsupported'):
        with safe_output.AtomicDirectory(target) as writer:
            writer.write_bytes('reference.jsonl', b'precomputed\n')
            writer.write_bytes('manifest.json', b'{}\n')
            writer.commit()
    assert target.is_dir() and (target/'reference.jsonl').read_bytes()==b'precomputed\n'
    assert not (target/'manifest.json').exists()
