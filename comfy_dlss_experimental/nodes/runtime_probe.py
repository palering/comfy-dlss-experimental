from __future__ import annotations

import json

from comfy_api.latest import io

from ..probe import probe_environment
from .types import RuntimeCatalog


class DLSSExperimentalRuntimeProbe(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalRuntimeProbe",
            display_name="DLSS 5 Runtime Probe",
            category="DLSS Experimental/Runtime",
            search_aliases=["dlss", "proton", "runtime probe", "environment"],
            description="Detects the host, NVIDIA driver, video tools, and user-installed Proton versions without loading external DLLs.",
            inputs=[io.Boolean.Input("include_diagnostics", default=False, advanced=True)],
            outputs=[RuntimeCatalog.Output("catalog"), io.String.Output("report")],
            is_experimental=True,
            not_idempotent=True,
        )

    @classmethod
    def execute(cls, include_diagnostics: bool) -> io.NodeOutput:
        report = probe_environment(include_diagnostics=include_diagnostics).to_dict()
        return io.NodeOutput(report, json.dumps(report, ensure_ascii=False, indent=2))
