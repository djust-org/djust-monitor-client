"""Tests for djust_monitor.context — thread-local context management."""

import threading

import pytest

from djust_monitor.context import (
    clear_context,
    get_context,
    pop_context,
    push_context,
    scoped_context,
    set_context,
)


# ---------------------------------------------------------------------------
# Fixture: always start each test with a clean slate
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean():
    clear_context()
    yield
    clear_context()


# ---------------------------------------------------------------------------
# get_context / set_context
# ---------------------------------------------------------------------------


class TestGetSetContext:
    def test_empty_by_default(self):
        assert get_context() == {}

    def test_set_single_key(self):
        set_context(pipeline_id="p-1")
        assert get_context() == {"pipeline_id": "p-1"}

    def test_set_multiple_keys_at_once(self):
        set_context(pipeline_id="p-1", stage_name="embed")
        ctx = get_context()
        assert ctx["pipeline_id"] == "p-1"
        assert ctx["stage_name"] == "embed"

    def test_set_merges_not_replaces(self):
        set_context(pipeline_id="p-1")
        set_context(stage_name="embed")
        ctx = get_context()
        assert ctx["pipeline_id"] == "p-1"
        assert ctx["stage_name"] == "embed"

    def test_set_overwrites_existing_key(self):
        set_context(pipeline_id="p-1")
        set_context(pipeline_id="p-2")
        assert get_context()["pipeline_id"] == "p-2"

    def test_set_context_with_none_value(self):
        set_context(user_id=None)
        assert get_context()["user_id"] is None


# ---------------------------------------------------------------------------
# clear_context
# ---------------------------------------------------------------------------


class TestClearContext:
    def test_clear_removes_all_keys(self):
        set_context(pipeline_id="p-1", stage_name="embed")
        clear_context()
        assert get_context() == {}

    def test_clear_after_push_removes_nested_scopes(self):
        push_context(agent_id="a-1")
        clear_context()
        assert get_context() == {}

    def test_clear_is_idempotent(self):
        clear_context()
        clear_context()
        assert get_context() == {}


# ---------------------------------------------------------------------------
# push_context / pop_context
# ---------------------------------------------------------------------------


class TestPushPopContext:
    def test_push_adds_nested_scope(self):
        set_context(pipeline_id="p-1")
        push_context(agent_id="a-7")
        ctx = get_context()
        assert ctx["pipeline_id"] == "p-1"
        assert ctx["agent_id"] == "a-7"

    def test_pop_removes_nested_scope(self):
        set_context(pipeline_id="p-1")
        push_context(agent_id="a-7")
        pop_context()
        ctx = get_context()
        assert "agent_id" not in ctx
        assert ctx["pipeline_id"] == "p-1"

    def test_pop_returns_the_popped_scope(self):
        push_context(agent_id="a-7", task_id="t-99")
        popped = pop_context()
        assert popped == {"agent_id": "a-7", "task_id": "t-99"}

    def test_nested_scope_overrides_parent(self):
        set_context(env="staging")
        push_context(env="production")
        assert get_context()["env"] == "production"

    def test_parent_survives_after_pop(self):
        set_context(env="staging")
        push_context(env="production")
        pop_context()
        assert get_context()["env"] == "staging"

    def test_multiple_nested_scopes(self):
        set_context(a=1)
        push_context(b=2)
        push_context(c=3)
        ctx = get_context()
        assert ctx == {"a": 1, "b": 2, "c": 3}
        pop_context()
        assert get_context() == {"a": 1, "b": 2}
        pop_context()
        assert get_context() == {"a": 1}

    def test_pop_on_empty_stack_raises_index_error(self):
        with pytest.raises(IndexError, match="pop_context"):
            pop_context()

    def test_pop_raises_after_all_nested_popped(self):
        push_context(x=1)
        pop_context()
        with pytest.raises(IndexError):
            pop_context()


# ---------------------------------------------------------------------------
# scoped_context context manager
# ---------------------------------------------------------------------------


class TestScopedContext:
    def test_scope_adds_keys(self):
        with scoped_context(agent_id="a-7"):
            assert get_context()["agent_id"] == "a-7"

    def test_scope_removed_on_exit(self):
        with scoped_context(agent_id="a-7"):
            pass
        assert "agent_id" not in get_context()

    def test_scope_removed_on_exception(self):
        with pytest.raises(RuntimeError):
            with scoped_context(agent_id="a-7"):
                raise RuntimeError("boom")
        assert "agent_id" not in get_context()

    def test_nested_scopes(self):
        with scoped_context(a=1):
            with scoped_context(b=2):
                assert get_context() == {"a": 1, "b": 2}
            assert get_context() == {"a": 1}
        assert get_context() == {}

    def test_scope_does_not_bleed_into_base(self):
        set_context(base="yes")
        with scoped_context(nested="yes"):
            pass
        assert get_context() == {"base": "yes"}

    def test_scope_yields_nothing(self):
        with scoped_context(x=42) as result:
            assert result is None


# ---------------------------------------------------------------------------
# Thread isolation
# ---------------------------------------------------------------------------


class TestThreadIsolation:
    def test_context_is_thread_local(self):
        results = {}

        def worker(name, value):
            clear_context()
            set_context(thread_key=value)
            # Simulate some work before reading back
            import time; time.sleep(0.01)
            results[name] = get_context().get("thread_key")

        t1 = threading.Thread(target=worker, args=("t1", "value1"))
        t2 = threading.Thread(target=worker, args=("t2", "value2"))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results["t1"] == "value1"
        assert results["t2"] == "value2"

    def test_push_in_one_thread_does_not_affect_another(self):
        barrier = threading.Barrier(2)
        results = {}

        def thread_a():
            clear_context()
            push_context(agent_id="a-only")
            barrier.wait()
            results["a"] = get_context()

        def thread_b():
            clear_context()
            barrier.wait()
            results["b"] = get_context()

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start(); tb.start()
        ta.join(); tb.join()

        assert "agent_id" in results["a"]
        assert "agent_id" not in results["b"]
