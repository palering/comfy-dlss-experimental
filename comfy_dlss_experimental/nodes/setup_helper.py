from __future__ import annotations

import json

from comfy_api.latest import io

from ..setup_check import inspect_setup
from .types import OpticalFlowProvider, RuntimeConfig, TemporalSequence


class DLSSExperimentalSetupHelper(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalSetupHelper",
            display_name="DLSS Setup Helper",
            category="DLSS Experimental/Runtime",
            search_aliases=["dlss", "doctor", "setup", "dependency", "环境检测", "依赖检查"],
            description="Checks the selected runtime, host tools, Python packages, platform bridge and optical-flow dependencies without launching the NR Worker/model. Passes the runtime through unchanged.",
            inputs=[
                RuntimeConfig.Input("runtime", tooltip="Connect the exact Runtime Configuration used by Preview/Process."),
                TemporalSequence.Input("sequence", optional=True,
                                       tooltip="Optional but recommended: checks the Input Adapter's media paths, selected guides and input readiness."),
                OpticalFlowProvider.Input("flow_provider", optional=True, advanced=True,
                                          tooltip="Use only when no Video Input Adapter is connected; checks this DIS/NVIDIA provider."),
                io.Boolean.Input("verify_nvidia_flow", default=False, optional=True, advanced=True,
                                 tooltip="When NVIDIA flow is selected, launch only our NVOF helper to query driver/GPU capability. The NR Worker/model are still not started."),
                io.Boolean.Input("include_diagnostics", default=False, optional=True, advanced=True,
                                 tooltip="Include fuller host command output. Paths and GPU details will appear in the copyable report."),
            ],
            outputs=[RuntimeConfig.Output("runtime"), io.Boolean.Output("ready"), io.String.Output("report")],
            is_output_node=True,
            is_experimental=True,
            not_idempotent=True,
        )

    @classmethod
    def execute(cls, runtime, sequence=None, flow_provider=None, verify_nvidia_flow=False, include_diagnostics=False):
        report = inspect_setup(runtime, sequence=sequence, flow_provider=flow_provider,
                               verify_nvidia_flow=verify_nvidia_flow, include_diagnostics=include_diagnostics)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        return io.NodeOutput(runtime, report["ready"], encoded, ui={"dlss_setup_report": [report]})
