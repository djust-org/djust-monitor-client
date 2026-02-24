"""Thread-local context management for djust_monitor.

Allows orchestrator code to set pipeline_id, task_id, stage_name, agent_id etc.
once at the start of a pipeline stage and have those fields automatically attached
to all exceptions captured in that thread — similar to Sentry's configure_scope().

Usage::

    import djust_monitor

    # Set flat context for the current thread
    djust_monitor.set_context(pipeline_id="p-123", stage_name="embed")

    # Nested scopes — useful when calling sub-routines that add their own context
    djust_monitor.push_context(agent_id="a-7")
    try:
        ...
    finally:
        djust_monitor.pop_context()

    # Clear everything for the current thread
    djust_monitor.clear_context()
"""

import threading
from contextlib import contextmanager

_local = threading.local()


def _stack() -> list[dict]:
    """Return (and lazily initialise) the per-thread scope stack."""
    if not hasattr(_local, "stack"):
        _local.stack = [{}]
    return _local.stack


def get_context() -> dict:
    """Return a merged view of all active scopes for the current thread.

    Later scopes override earlier ones (deepest push wins).
    """
    merged: dict = {}
    for scope in _stack():
        merged.update(scope)
    return merged


def set_context(**kwargs) -> None:
    """Set key/value pairs in the current (outermost) scope.

    Calling ``set_context`` multiple times *merges* — it does not replace the
    entire scope, only the supplied keys.
    """
    _stack()[0].update(kwargs)


def push_context(**kwargs) -> None:
    """Push a new nested scope with the supplied key/value pairs.

    Nested scopes overlay the parent scope; ``pop_context`` removes them.
    """
    _stack().append(dict(kwargs))


def pop_context() -> dict:
    """Pop the innermost nested scope and return it.

    Raises ``IndexError`` if there are no nested scopes (only the base scope
    remains).
    """
    stack = _stack()
    if len(stack) <= 1:
        raise IndexError("pop_context() called with no nested scopes to pop")
    return stack.pop()


def clear_context() -> None:
    """Remove all context from the current thread (resets to an empty base scope)."""
    _local.stack = [{}]


@contextmanager
def scoped_context(**kwargs):
    """Context manager that pushes a scope on entry and pops it on exit.

    Example::

        with djust_monitor.scoped_context(agent_id="a-7", task_id="t-99"):
            run_agent()
    """
    push_context(**kwargs)
    try:
        yield
    finally:
        pop_context()
