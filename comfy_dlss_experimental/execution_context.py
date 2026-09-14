"""Host-supplied execution services, independent of ComfyUI and graphics APIs.

Paths are explicit for standalone callers. The context is synchronous and
task-local; it does not start a service, relax storage limits, or grant multiple
processes ownership of the same GPU session. Callbacks are in-process adapters,
not a serialized public RPC contract.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Callable


def _not_cancelled():
    return False


def _no_progress(_stage, _done, _total):
    pass


@dataclass(frozen=True)
class ExecutionContext:
    data_root: Path
    temp_root: Path
    cancelled: Callable[[], bool] = _not_cancelled
    progress: Callable[[str, int, int], None] = _no_progress

    def __post_init__(self):
        for name in ("data_root", "temp_root"):
            value = Path(getattr(self, name)).expanduser()
            if not value.is_absolute():
                raise ValueError(f"ExecutionContext.{name} must be an absolute path")
            object.__setattr__(self, name, value.resolve())
        if not callable(self.cancelled) or not callable(self.progress):
            raise TypeError("Execution callbacks must be callable")


_active: ContextVar[ExecutionContext | None] = ContextVar("dlss_host_execution", default=None)


def current_execution_context():
    return _active.get()


@contextmanager
def execution_scope(context):
    if not isinstance(context, ExecutionContext):
        raise TypeError("An explicit ExecutionContext is required")
    token = _active.set(context)
    try:
        if context.cancelled():
            raise InterruptedError("DLSS processing cancelled")
        yield context
    finally:
        _active.reset(token)


def with_execution_context(function):
    @wraps(function)
    def call(*args, context, **kwargs):
        with execution_scope(context):
            return function(*args, context=context, **kwargs)
    return call
