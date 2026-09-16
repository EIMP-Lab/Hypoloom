"""Ephemeral Host reads must see commits and never leak mutated cached rows."""
import pytest

from hypoloom.state_store import HostWriterCapability, StateStore


def test_read_scope_reuses_reads_but_sees_commits_rollbacks_and_mutation(tmp_path):
    with StateStore(tmp_path / "state", HostWriterCapability.issue()) as store:
        with store.transaction() as tx:
            tx.execute("CREATE TABLE read_reuse_example(value INTEGER)")
            tx.execute("INSERT INTO read_reuse_example VALUES(1)")
        sql = "SELECT value FROM read_reuse_example"
        with store.cache_reads():
            first = store.inspect(sql)
            first[0]["value"] = 999
            assert store.inspect(sql)[0]["value"] == 1
            with store.transaction() as tx:
                tx.execute("UPDATE read_reuse_example SET value=2")
                assert store.inspect(sql)[0]["value"] == 1
            assert store.inspect(sql)[0]["value"] == 2
            with pytest.raises(RuntimeError):
                with store.transaction() as tx:
                    tx.execute("UPDATE read_reuse_example SET value=3")
                    raise RuntimeError("rollback")
            assert store.inspect(sql)[0]["value"] == 2
        assert not store._read_cache_active


def test_unchanged_idle_ticks_reuse_reads_but_writes_outside_scope_invalidate(tmp_path):
    reads = []
    with StateStore(tmp_path / "state", HostWriterCapability.issue()) as store:
        with store.transaction() as tx:
            tx.execute("CREATE TABLE read_reuse_example(value INTEGER)")
            tx.execute("INSERT INTO read_reuse_example VALUES(1)")
        def read():
            reads.append(1)
            return store.inspect("SELECT value FROM read_reuse_example")
        for _ in range(3):
            with store.cache_reads():
                assert store.cached_read("example", read) == ({"value": 1},)
        assert len(reads) == 1
        with store.transaction() as tx:
            tx.execute("UPDATE read_reuse_example SET value=2")
        with store.cache_reads():
            assert store.cached_read("example", read) == ({"value": 2},)
        assert len(reads) == 2


def test_read_scope_does_not_cache_an_independent_reader(tmp_path):
    root = tmp_path / "state"
    with StateStore(root, HostWriterCapability.issue()) as writer:
        with writer.transaction() as tx:
            tx.execute("CREATE TABLE read_reuse_example(value INTEGER)")
            tx.execute("INSERT INTO read_reuse_example VALUES(1)")
        with StateStore(root) as reader, reader.cache_reads():
            assert reader.inspect("SELECT value FROM read_reuse_example")[0]["value"] == 1
            with writer.transaction() as tx:
                tx.execute("UPDATE read_reuse_example SET value=2")
            assert reader.inspect("SELECT value FROM read_reuse_example")[0]["value"] == 2
