"""Confined traversal regressions, including the independent merge review cases."""
import os
from contextlib import contextmanager, nullcontext

import pytest

from conftest import requires_confinement
from memcore import reading
from memcore.reading import (ReadLimitError, confined_reads, open_text, read_file_bytes, read_input_bytes,
                             read_limits, walk_files)
from memfalsify.corpus import scan


@pytest.fixture
def wrap_scandir_entries(monkeypatch):
    original = os.scandir

    def wrap(entry_type):
        class WrappedIterator:
            def __init__(self, path):
                self.iterator = original(path)

            def __enter__(self):
                self.iterator.__enter__()
                return self

            def __exit__(self, *args):
                return self.iterator.__exit__(*args)

            def __iter__(self):
                return self

            def __next__(self):
                return entry_type(next(self.iterator))

            def close(self):
                self.iterator.close()

        monkeypatch.setattr(os, "scandir", WrappedIterator)

    return wrap


class UnknownTypeEntry:
    """Model DT_UNKNOWN, requiring a stat rather than a cached directory type."""
    def __init__(self, entry):
        self.entry = entry

    def __getattr__(self, name):
        return getattr(self.entry, name)

    def is_dir(self, *, follow_symlinks=True):
        self.entry.stat(follow_symlinks=follow_symlinks)
        return self.entry.is_dir(follow_symlinks=follow_symlinks)


@pytest.mark.parametrize("confined", [False, pytest.param(True, marks=requires_confinement)])
def test_scan_fails_closed_on_uncached_entry_type(tmp_path, wrap_scandir_entries, confined):
    """Port of the review's real permission-denial / DT_UNKNOWN reproduction."""
    root = tmp_path.resolve()
    docs = root / "docs"
    docs.mkdir()
    (docs / "counter.md").write_text("The rate limit is five requests per minute.\n")
    (root / "safe.md").write_text("The rate limit is one hundred requests per minute.\n")
    wrap_scandir_entries(UnknownTypeEntry)
    docs.chmod(0o400)  # Listing allowed, but metadata lookups need search permission.
    try:
        if os.access(docs, os.X_OK):
            pytest.skip("this user can bypass directory search permissions")
        with confined_reads(root) if confined else nullcontext():
            with pytest.raises(PermissionError):
                scan(root)
    finally:
        docs.chmod(0o700)


@pytest.mark.parametrize("operation", ["is_dir", "is_symlink"])
@requires_confinement
def test_confined_classification_error_is_reported_and_entry_retained(
    tmp_path, wrap_scandir_entries, operation,
):
    """Injected errors also cover hosts/users that bypass chmod restrictions."""
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    (root / "safe.md").write_text("Accessible evidence.\n")
    failure = PermissionError("cannot classify docs")

    class FailedEntry(UnknownTypeEntry):
        def is_dir(self, *, follow_symlinks=True):
            if self.name == "docs" and operation == "is_dir":
                raise failure
            return super().is_dir(follow_symlinks=follow_symlinks)

        def is_symlink(self):
            if self.name == "docs" and operation == "is_symlink":
                raise failure
            return self.entry.is_symlink()

    wrap_scandir_entries(FailedEntry)
    errors = []
    with confined_reads(root):
        with pytest.raises(PermissionError, match="cannot classify docs"):
            scan(root)
        rows = list(walk_files(root, onerror=errors.append))
        # A callback that returns must not allow the entry to disappear either.
        assert len(rows) == 1
        assert rows[0][:2] == (root, [])
        assert set(rows[0][2]) == {"docs", "safe.md"}
        with pytest.raises(PermissionError, match="cannot classify docs"):
            list(walk_files(root))
    assert errors == [failure]


@pytest.mark.parametrize("operation", ["scandir", "iteration", "open"])
@pytest.mark.parametrize("target", ["root", "child"])
@requires_confinement
def test_confined_traversal_error_calls_onerror(tmp_path, monkeypatch, operation, target):
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    target_inode = (root if target == "root" else root / "docs").stat().st_ino
    failure = PermissionError("cannot traverse evidence")
    original_open, original_scandir = os.open, os.scandir
    errors = []

    def guarded_open(path, *args, **kwargs):
        if path == (root.name if target == "root" else "docs"):
            raise failure
        return original_open(path, *args, **kwargs)

    class FailedIterator:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            raise failure

    def guarded_scandir(descriptor):
        assert isinstance(descriptor, int), "confined traversal must use a directory fd"
        if os.fstat(descriptor).st_ino == target_inode:
            if operation == "scandir":
                raise failure
            return FailedIterator()
        return original_scandir(descriptor)

    def onerror(error):
        errors.append(error)
        raise error

    with confined_reads(root.parent):
        monkeypatch.setattr(os, "open" if operation == "open" else "scandir",
                            guarded_open if operation == "open" else guarded_scandir)
        with pytest.raises(PermissionError, match="cannot traverse evidence"):
            list(walk_files(root, onerror=onerror))
    assert errors == [failure]


@pytest.mark.parametrize("pattern", ["docs/**", "docs/*", "docs", "./docs/"])
@requires_confinement
def test_confined_exclusion_prunes_before_open(tmp_path, monkeypatch, pattern):
    root = tmp_path.resolve()
    (root / "docs" / "nested").mkdir(parents=True)
    (root / "docs" / "nested" / "api.md").write_text("This is counterevidence.\n")
    (root / "safe.md").write_text("This is accessible evidence.\n")
    (root / "alias").symlink_to("docs", target_is_directory=True)
    original = os.open

    def guarded(path, *args, **kwargs):
        assert path != "docs", "excluded subtree was opened"
        return original(path, *args, **kwargs)

    with confined_reads(root):
        monkeypatch.setattr(os, "open", guarded)
        passages, skipped = scan(root, exclude=[pattern, "alias/**"])
    assert {p.span.path for p in passages} == {"safe.md"}
    assert skipped == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO tests require os.mkfifo")
@pytest.mark.parametrize("confined", [False, pytest.param(True, marks=requires_confinement)])
def test_links_and_special_files(tmp_path, confined):
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    (root / "docs" / "api.md").write_text("The rate limit is five requests per minute.\n")
    (root / "alias").symlink_to("docs", target_is_directory=True)
    (root / "outside").symlink_to(root.parent, target_is_directory=True)
    (root / "broken.md").symlink_to("absent.md")
    (root / "cycle.md").symlink_to("cycle.md")
    os.mkfifo(root / "pipe.txt")
    with confined_reads(root) if confined else nullcontext():
        passages, skipped = scan(root)
    assert {p.span.path for p in passages} == {"docs/api.md"}
    assert {s.path: s.reason for s in skipped} == {
        "alias": "symlinked directory not followed",
        "outside": "symlinked directory not followed",
        "broken.md": "broken or cyclic symlink",
        "cycle.md": "broken or cyclic symlink",
        "pipe.txt": "not a regular file",
    }


@requires_confinement
def test_confined_walk_lists_directory_links_without_descending(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    (root / "alias").symlink_to("docs", target_is_directory=True)
    (root / "outside").symlink_to(root.parent, target_is_directory=True)
    original = os.open

    def guarded(path, flags, *args, **kwargs):
        assert path not in {"alias", "outside"}, "directory symlink was opened"
        assert flags & os.O_NOFOLLOW
        return original(path, flags, *args, **kwargs)

    with confined_reads(root):
        monkeypatch.setattr(os, "open", guarded)
        rows = list(walk_files(root))
    assert set(rows[0][1]) == {"docs", "alias", "outside"}
    assert [row[0] for row in rows] == [root, root / "docs"]


@requires_confinement
def test_confined_walk_keeps_descriptors_pinned_across_yields(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "inside.md").write_text("Inside evidence.\n")
    (outside / "docs").mkdir(parents=True)
    (outside / "docs" / "private-filename.md").write_text("Outside evidence.\n")
    root, outside = root.resolve(), outside.resolve()
    with confined_reads(root):
        walker = walk_files(root)
        try:
            assert next(walker) == (root, ["docs"], [])
            root.rename(root.with_name("previous-root"))
            root.symlink_to(outside, target_is_directory=True)
            assert list(walker) == [(root / "docs", [], ["inside.md"])]
        finally:
            walker.close()


@requires_confinement
def test_confined_walk_rejects_directory_swap_before_descent(tmp_path, monkeypatch):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "docs").mkdir(parents=True)
    outside.mkdir()
    (outside / "private-filename.md").write_text("Outside evidence.\n")
    original = os.open
    called, errors = [], []

    def swap_then_open(path, flags, *args, **kwargs):
        if path == "docs":
            called.append(path)
            (root / "docs").rename(root / "previous-docs")
            (root / "docs").symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    def onerror(error):
        errors.append(error)
        raise error

    with confined_reads(root):
        monkeypatch.setattr(os, "open", swap_then_open)
        with pytest.raises(OSError):
            list(walk_files(root, onerror=onerror))
    assert called == ["docs"]
    assert len(errors) == 1


# --- size and traversal limits (memlab-ui sets them per request) ---------------------------------------------

def _context(confined, root):
    return confined_reads(root) if confined else nullcontext()


@pytest.mark.parametrize("confined", [False, pytest.param(True, marks=requires_confinement)])
def test_max_bytes_refuses_a_larger_file_without_reading_it(tmp_path, monkeypatch, confined):
    (tmp_path / "small.md").write_bytes(b"x" * 10)
    (tmp_path / "large.md").write_bytes(b"x" * 11)
    with _context(confined, tmp_path):
        assert read_input_bytes(tmp_path, tmp_path / "small.md", max_bytes=10) == b"x" * 10
        with pytest.raises(ReadLimitError) as refused:
            read_input_bytes(tmp_path, tmp_path / "large.md", max_bytes=10)
    assert refused.value.budget is False
    # Without max_bytes nothing changes.
    assert read_input_bytes(tmp_path, tmp_path / "large.md") == b"x" * 11


@requires_confinement  # the Windows branch reads from a pinned handle the test cannot wrap
@pytest.mark.parametrize("confined", [False, True])
def test_a_file_that_grows_during_the_read_is_refused_after_limit_plus_one(tmp_path, monkeypatch, confined):
    (tmp_path / "growing.md").write_bytes(b"x" * 1000)
    real_fstat = os.fstat

    class Small:  # fstat ran before the file grew
        def __init__(self, result):
            self.result = result

        def __getattr__(self, name):
            return 5 if name == "st_size" else getattr(self.result, name)

    monkeypatch.setattr(reading.os, "fstat", lambda fd: Small(real_fstat(fd)))
    sizes = []
    original = reading._open

    class Recorder:
        def __init__(self, handle):
            self.handle = handle

        def read(self, size=-1):
            sizes.append(size)
            return self.handle.read(size)

        def fileno(self):
            return self.handle.fileno()

    @contextmanager
    def recording(path, encoding=None):
        with original(path, encoding) as handle:
            yield Recorder(handle)

    monkeypatch.setattr(reading, "_open", recording)
    with _context(confined, tmp_path), pytest.raises(ReadLimitError):
        read_input_bytes(tmp_path, tmp_path / "growing.md", max_bytes=10)
    assert sizes == [11]


@requires_confinement
def test_read_limits_bound_files_total_bytes_and_directories(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "notes.md").write_text("The rate limit is 60 per minute.\n" * 3, encoding="utf-8")
    (tmp_path / "big.md").write_text("word " * 200, encoding="utf-8")
    with confined_reads(tmp_path), read_limits(file_bytes=500) as limits:
        with pytest.raises(ReadLimitError):
            read_file_bytes(tmp_path / "big.md")
        with pytest.raises(ReadLimitError):
            with open_text(tmp_path / "big.md"):
                pass
    assert not limits.truncated
    with confined_reads(tmp_path), read_limits(total_bytes=150) as limits:
        assert len(read_file_bytes(tmp_path / "a" / "notes.md")) == 99
        with pytest.raises(ReadLimitError) as refused:
            read_file_bytes(tmp_path / "b" / "notes.md")
    assert refused.value.budget and limits.truncated and limits.bytes_read == 99
    with confined_reads(tmp_path), read_limits(directories=2) as limits:
        walked = [current for current, _dirs, _files in walk_files(tmp_path)]
    assert len(walked) == 2 and limits.truncated
    with confined_reads(tmp_path), read_limits(total_bytes=150) as limits:
        passages, skipped = scan(tmp_path)
    assert limits.truncated
    assert any(s.reason == "not read: the read budget for this request is used up" for s in skipped)
    assert passages


def test_walk_limits_apply_without_confinement_too(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    with read_limits(directories=3) as limits:
        assert len(list(walk_files(tmp_path))) == 3
    assert limits.truncated
    assert len(list(walk_files(tmp_path))) == 4  # no limits outside the context


def test_scan_skips_files_over_two_megabytes_while_reading(tmp_path, monkeypatch):
    (tmp_path / "huge.md").write_bytes(b"The limit is 60.\n" * 130_000)
    (tmp_path / "ok.md").write_text("The limit is 60.\n", encoding="utf-8")
    largest = []
    original = reading._read_all

    def spy(handle, max_bytes=None):
        largest.append(max_bytes)
        return original(handle, max_bytes)

    monkeypatch.setattr(reading, "_read_all", spy)
    passages, skipped = scan(tmp_path)
    assert {"path": "huge.md", "reason": "larger than 2 MB"} in [s.model_dump() for s in skipped]
    assert set(largest) == {2_000_000} and [p.span.path for p in passages] == ["ok.md"]
