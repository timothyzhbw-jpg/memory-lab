"""Confined traversal regressions, including the independent merge review cases."""
import hashlib
import io
import os
from contextlib import contextmanager, nullcontext

import pytest

from conftest import requires_confinement, write_text_exact
from memcore import SidecarError, reading
from memcore.jsonl import read_jsonl
from memcore.reading import (ReadLimitError, confined_reads, open_text, read_file_bytes, read_file_text,
                             read_input_bytes, read_limits, walk_files)
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
    write_text_exact(docs / "counter.md", "The rate limit is five requests per minute.\n")
    write_text_exact(root / "safe.md", "The rate limit is one hundred requests per minute.\n")
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
    write_text_exact(root / "safe.md", "Accessible evidence.\n")
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
    write_text_exact(root / "docs" / "nested" / "api.md", "This is counterevidence.\n")
    write_text_exact(root / "safe.md", "This is accessible evidence.\n")
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
    write_text_exact(root / "docs" / "api.md", "The rate limit is five requests per minute.\n")
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
    write_text_exact(root / "docs" / "inside.md", "Inside evidence.\n")
    (outside / "docs").mkdir(parents=True)
    write_text_exact(outside / "docs" / "private-filename.md", "Outside evidence.\n")
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
    write_text_exact(outside / "private-filename.md", "Outside evidence.\n")
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
        write_text_exact(tmp_path / name / "notes.md", "The rate limit is 60 per minute.\n" * 3)
    write_text_exact(tmp_path / "big.md", "word " * 200)
    with confined_reads(tmp_path), read_limits(file_bytes=500) as limits:
        with pytest.raises(ReadLimitError):
            read_file_bytes(tmp_path / "big.md")
        with pytest.raises(ReadLimitError):
            with open_text(tmp_path / "big.md"):
                pass
    assert limits.truncated  # the page's own per-file limit refused it: its view is incomplete
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
    write_text_exact(tmp_path / "ok.md", "The limit is 60.\n")
    largest = []
    original = reading._read_all

    def spy(handle, max_bytes=None):
        largest.append(max_bytes)
        return original(handle, max_bytes)

    monkeypatch.setattr(reading, "_read_all", spy)
    passages, skipped = scan(tmp_path)
    assert {"path": "huge.md", "reason": "larger than 2 MB"} in [s.model_dump() for s in skipped]
    assert set(largest) == {2_000_000} and [p.span.path for p in passages] == ["ok.md"]


# --- round 3: bytes, streams, git objects, named budgets and deep trees ---------------------------------------

@requires_confinement
def test_text_reads_are_charged_in_bytes_not_characters(tmp_path):
    write_text_exact(tmp_path / "emoji.txt", "\U0001F600\U0001F600\U0001F600")  # 3 characters, 12 bytes
    with confined_reads(tmp_path), read_limits(file_bytes=10):
        with pytest.raises(ReadLimitError):
            read_file_text(tmp_path / "emoji.txt")
    with confined_reads(tmp_path), read_limits(total_bytes=100) as limits:
        assert read_file_text(tmp_path / "emoji.txt") == "\U0001F600" * 3
    assert limits.bytes_read == 12


@requires_confinement
def test_text_reads_within_limits_return_what_they_did_before(tmp_path):
    (tmp_path / "mixed.txt").write_bytes(b"one\r\ntwo\rthree\nfour")
    with confined_reads(tmp_path):
        plain = read_file_text(tmp_path / "mixed.txt")
        with open_text(tmp_path / "mixed.txt") as handle:
            lines = list(handle)
        with read_limits(file_bytes=1000, total_bytes=1000):
            assert read_file_text(tmp_path / "mixed.txt") == plain
            with open_text(tmp_path / "mixed.txt") as handle:
                assert list(handle) == lines


@requires_confinement
def test_streams_are_charged_as_they_are_read_even_if_the_size_lies(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    write_text_exact(path, "".join('{"n": %d}\n' % i for i in range(1000)))  # about 9,000 bytes
    real_fstat = os.fstat

    class Small:  # fstat ran before the file grew to its real size
        def __init__(self, result):
            self.result = result

        def __getattr__(self, name):
            return 8 if name == "st_size" else getattr(self.result, name)

    monkeypatch.setattr(reading.os, "fstat", lambda fd: Small(real_fstat(fd)))
    with confined_reads(tmp_path), read_limits(total_bytes=1000) as limits:
        with pytest.raises(ReadLimitError) as refused:
            list(read_jsonl(path))
    assert refused.value.budget and limits.truncated and limits.bytes_read <= 1001
    with confined_reads(tmp_path), read_limits(file_bytes=1000) as limits:
        with pytest.raises(ReadLimitError):
            list(read_jsonl(path))
    monkeypatch.undo()
    assert len(list(read_jsonl(path))) == 1000  # unbounded outside read_limits


def test_git_objects_are_charged_before_their_bytes_are_read():
    from memfalsify.gitstore import read_batch_object

    stdout = io.BytesIO(b"0123 blob 100\n" + b"x" * 100 + b"\n")
    with read_limits(total_bytes=50) as limits, pytest.raises(ReadLimitError):
        read_batch_object(stdout)
    assert limits.truncated and stdout.tell() == len(b"0123 blob 100\n")
    stdout.seek(0)
    with read_limits(total_bytes=500) as limits:
        assert read_batch_object(stdout)[2] == b"x" * 100
    assert limits.bytes_read == 100


def test_named_budgets_bound_claims_passages_and_comparisons(tmp_path):
    from memfalsify import parse_memory
    from memfalsify.audit import shortlist

    write_text_exact(tmp_path / "MEMORY.md", "".join(f"- Service {i} answers within {i} seconds.\n" for i in range(20)))
    with read_limits(items={"claims": 5}) as limits:
        claims, _ = parse_memory(tmp_path / "MEMORY.md")
    assert len(claims) == 5 and limits.exhausted == {"claims"}
    assert len(parse_memory(tmp_path / "MEMORY.md")[0]) == 20
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        write_text_exact(evidence / name, "Service one answers within one second. Service two answers fast.\n" * 3)
    with read_limits(items={"passages": 4}) as limits:
        passages, skipped = scan(evidence)
    assert len(passages) == 4 and "passages" in limits.exhausted
    assert {s.reason for s in skipped} == {"not read: the read budget for this request is used up"}
    everything, _ = scan(evidence)
    with read_limits(items={"comparisons": 2}) as limits:
        assert len(shortlist(claims[1], everything, min_coverage=0.0)) <= 2
    assert "comparisons" in limits.exhausted


@requires_confinement
def test_directory_entries_are_counted_while_listing(tmp_path):
    for i in range(10):
        write_text_exact(tmp_path / f"f{i}.md", "x")
    with confined_reads(tmp_path), read_limits(entries=4) as limits:
        (_, _, files), = list(walk_files(tmp_path))
    assert len(files) == 4 and limits.exhausted == {"entries"}


def _deep(base, levels):
    """``levels`` nested directories, made relative to descriptors so no absolute path gets long."""
    fd = os.open(base, os.O_RDONLY)
    try:
        for _ in range(levels):
            os.mkdir("d", dir_fd=fd)
            child = os.open("d", os.O_RDONLY, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


@requires_confinement
def test_deep_trees_walk_without_recursion_and_stop_at_the_depth_limit(tmp_path, monkeypatch):
    _deep(tmp_path, 150)
    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(100)  # a recursive walk would fail long before 150 levels
    try:
        with confined_reads(tmp_path):
            assert len(list(walk_files(tmp_path))) == 151
            with read_limits(depth=64) as limits:
                walked = list(walk_files(tmp_path))
    finally:
        sys.setrecursionlimit(old)
    assert len(walked) == 65 and limits.exhausted == {"depth"}


# --- round 4: consumed bytes, media hashing, os.walk entries, the pinned-handle fallback ----------------------

class _LyingSize:
    """An fstat result whose size is what it was before the file grew."""

    def __init__(self, result, size):
        self.result, self.size = result, size

    def __getattr__(self, name):
        return self.size if name == "st_size" else getattr(self.result, name)


@requires_confinement
def test_refused_reads_are_charged_and_mark_the_view_incomplete(tmp_path, monkeypatch):
    path = tmp_path / "grows.jsonl"
    write_text_exact(path, "".join('{"n": %d}\n' % i for i in range(200)))
    real_fstat = os.fstat
    monkeypatch.setattr(reading.os, "fstat", lambda fd: _LyingSize(real_fstat(fd), 8))
    with confined_reads(tmp_path), read_limits(file_bytes=100) as limits:
        with pytest.raises(ReadLimitError):
            list(read_jsonl(path))
    assert limits.truncated and 100 < limits.bytes_read <= 101  # what was consumed is charged
    with confined_reads(tmp_path), read_limits(file_bytes=10) as limits:
        with pytest.raises(ReadLimitError):
            read_file_bytes(path)
    assert limits.truncated and limits.bytes_read == 11


@requires_confinement
def test_a_callers_own_limit_is_a_skip_not_an_incomplete_view(tmp_path):
    (tmp_path / "big.md").write_bytes(b"x" * 50)
    with confined_reads(tmp_path), read_limits(file_bytes=1000, total_bytes=1000) as limits:
        with pytest.raises(ReadLimitError):
            read_input_bytes(tmp_path, tmp_path / "big.md", max_bytes=10)
    assert not limits.truncated


def test_the_pinned_handle_fallback_enforces_and_charges_the_limits(tmp_path, monkeypatch):
    """read_input_bytes without O_NOFOLLOW (Windows) reads from a pinned handle; exercise it everywhere."""
    monkeypatch.setattr(reading, "confinement_supported", lambda: False)
    (tmp_path / "small.md").write_bytes(b"x" * 10)
    (tmp_path / "large.md").write_bytes(b"x" * 11)
    assert read_input_bytes(tmp_path, tmp_path / "small.md", max_bytes=10) == b"x" * 10
    with pytest.raises(ReadLimitError):
        read_input_bytes(tmp_path, tmp_path / "large.md", max_bytes=10)
    (tmp_path / "grows.md").write_bytes(b"x" * 1000)
    real_fstat = os.fstat
    monkeypatch.setattr(reading.os, "fstat", lambda fd: _LyingSize(real_fstat(fd), 5))
    with read_limits(total_bytes=10_000) as limits, pytest.raises(ReadLimitError):
        read_input_bytes(tmp_path, tmp_path / "grows.md", max_bytes=10)
    assert limits.bytes_read == 11 and not limits.truncated  # max_bytes is the caller's own limit
    monkeypatch.setattr(reading.os, "fstat", real_fstat)
    with read_limits(total_bytes=15) as limits:
        assert read_input_bytes(tmp_path, tmp_path / "small.md") == b"x" * 10
        with pytest.raises(ReadLimitError) as refused:
            read_input_bytes(tmp_path, tmp_path / "large.md")
    assert refused.value.budget and limits.truncated


def test_walk_entries_are_counted_without_confinement_too(tmp_path):
    for i in range(6):
        write_text_exact(tmp_path / f"f{i}.md", "x")
    (tmp_path / "sub").mkdir()
    with read_limits(entries=4) as limits:
        _, dirs, files = list(walk_files(tmp_path))[0]
    assert len(dirs) + len(files) == 4 and dirs == ["sub"] and limits.exhausted == {"entries"}


@requires_confinement
def test_media_hashing_obeys_the_page_limits(tmp_path):
    from memcore import load_sidecar

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    media = b"\x00" * 5000
    (evidence / "clip.mp4").write_bytes(media)
    digest = hashlib.sha256(media).hexdigest()
    sidecar = (f"WEBVTT\n\nNOTE source_sha256: {digest}\n\n00:00.000 --> 00:02.000\n"
               "The public API rate limit is 60 requests per minute.\n")
    write_text_exact(evidence / "clip.mp4.vtt", sidecar)
    assert load_sidecar(evidence, "clip.mp4.vtt", sidecar).media_sha256 == digest  # unlimited outside a page
    with confined_reads(tmp_path), read_limits(file_bytes=1000) as limits:
        with pytest.raises(SidecarError, match="over this page's read limit"):
            load_sidecar(evidence, "clip.mp4.vtt", sidecar)
    assert limits.truncated
    with confined_reads(tmp_path), read_limits(file_bytes=100_000, total_bytes=3000) as limits:
        passages, skipped = scan(evidence)
    assert not passages and any("over this page's read limit" in s.reason for s in skipped)
    with confined_reads(tmp_path), read_limits(file_bytes=100_000, total_bytes=100_000) as limits:
        passages, _ = scan(evidence)
    assert len(passages) == 1 and limits.bytes_read >= 5000  # the hash is charged


def test_an_exhausted_budget_that_skips_something_marks_the_view_incomplete():
    with read_limits(items={"passages": 1}) as limits:
        assert reading.take("passages") == 1 and not limits.truncated
        assert not reading.available("passages") and limits.truncated
