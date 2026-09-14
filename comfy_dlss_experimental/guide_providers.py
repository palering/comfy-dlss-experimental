"""Selected-only estimator construction, independent of temporal orchestration."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .flow_provider import DISFlow, FlowProvider


class FlowEstimator(Protocol):
    """Analysis-grid XY displacement in pixels, current->previous then reverse.

The temporal layer owns dense resize, unit conversion, diagnostics and packing.
Providers own their estimator/session and reset/close its temporal resources.
Array types are deliberately not imported while merely configuring a workflow.
"""

    def estimate(self, current: Any, previous: Any) -> tuple[Any, Any]: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


def create_flow_estimator(
    kind: str, width: int, height: int, config: FlowProvider | None = None,
    *, cancelled: Callable[[], bool] = lambda: False,
) -> FlowEstimator | None:
    """Construct one chosen provider; zero mode creates nothing, failures propagate.

This is an execution boundary, not discovery: neither helper probing nor imports
of the unselected native implementation take place here.
"""
    if kind not in ("zero", "dis", "nvidia"):
        raise ValueError("Unsupported motion provider")
    if config is not None:
        if not isinstance(config, FlowProvider):
            raise ValueError("Invalid flow provider configuration")
        config.validate()
        if config.kind != kind:
            raise ValueError("Flow provider and motion mode disagree")
    if kind == "zero":
        return None
    config = config or FlowProvider(kind=kind)
    if kind == "dis":
        return DISFlow(config)
    from .nvidia_flow import NvidiaFlow
    return NvidiaFlow(width, height, config, cancelled=cancelled)
