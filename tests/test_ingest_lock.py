"""Cross-importer locks fail promptly and exclude concurrent writers, including simultaneous starts."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from orgmemory import github_apply, ingest, jira_apply


@pytest.mark.parametrize("first, second", [(github_apply.lock, jira_apply.lock),
                                         (jira_apply.lock, github_apply.lock)])
def test_other_importer_refuses_a_live_writer_and_can_retry(tmp_path, first, second):
    entered, release = Event(), Event()

    def writer():
        with first(tmp_path):
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(writer)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match="another sync is running"):
                with second(tmp_path):
                    pytest.fail("a second writer acquired the lock")
        finally:
            release.set()
        pending.result(timeout=5)
    with second(tmp_path):
        pass
    assert list(tmp_path.iterdir()) == []


def test_simultaneous_importer_starts_cannot_deadlock_or_overlap(tmp_path, monkeypatch):
    # The lock files alone, as where directory locks are unavailable (Windows); POSIX adds a directory lock.
    monkeypatch.setattr(ingest, "DIRECTORY_LOCKS", False)
    created, checking = Barrier(2), Barrier(2)
    original_open, original_exists = ingest.os.open, ingest.Path.exists
    mutex = Lock()
    active = 0

    def open_together(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path.name in ingest.LOCKS:
            try:
                created.wait(timeout=5)
            except BaseException:
                ingest.os.close(fd)
                raise
        return fd

    def exists_together(path):
        result = original_exists(path)
        if path.name in ingest.LOCKS:
            # Both checks happen while both lock files still exist.
            checking.wait(timeout=5)
        return result

    def attempt(lock):
        nonlocal active
        try:
            with lock(tmp_path):
                with mutex:
                    active += 1
                    assert active == 1
                    active -= 1
        except ValueError as exc:
            assert str(exc) == ingest.LOCK_HELD
            return "refused"
        return "acquired"

    with monkeypatch.context() as m:
        m.setattr(ingest.os, "open", open_together)
        m.setattr(ingest.Path, "exists", exists_together)
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(attempt, lock) for lock in (github_apply.lock, jira_apply.lock)]
            assert [future.result(timeout=5) for future in attempts] == ["refused", "refused"]
    assert active == 0 and list(tmp_path.iterdir()) == []
    with github_apply.lock(tmp_path):
        pass
    with jira_apply.lock(tmp_path):
        pass


@pytest.mark.skipif(not ingest.DIRECTORY_LOCKS, reason="directory locks need POSIX flock and dir_fd")
def test_directory_lock_excludes_writers_even_without_the_lock_file(tmp_path):
    # Deleting the marker while an importer runs must not admit a second writer.
    with github_apply.lock(tmp_path):
        (tmp_path / ".github-sync.lock").unlink()
        for lock in (github_apply.lock, jira_apply.lock):
            with pytest.raises(ValueError, match="another sync is running"):
                with lock(tmp_path):
                    pytest.fail("a second writer acquired the lock")
    assert list(tmp_path.iterdir()) == []
    with jira_apply.lock(tmp_path):
        pass


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_importer_locks_never_follow_a_planted_marker_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / ".jira-sync.lock").symlink_to(outside)
    with pytest.raises(ValueError, match="another sync is running"):
        with jira_apply.lock(dataset):
            pytest.fail("acquired through a symlinked marker")
    assert outside.read_bytes() == b"keep" and (dataset / ".jira-sync.lock").is_symlink()
