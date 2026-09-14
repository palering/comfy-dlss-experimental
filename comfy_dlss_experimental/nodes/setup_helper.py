from __future__ import annotations

import json

from comfy_api.latest import io
from ..diagnostic_ui import diagnostic_ui

from ..setup_check import inspect_setup
from ..media_pipeline import require_pipeline, pipeline_report
from .types import MediaPipelineType, OpticalFlowProvider, RuntimeConfig, TemporalSequence


class DLSSExperimentalSetupHelper(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalSetupHelper",
            display_name="DLSS Setup Helper",
            category="DLSS Experimental/Runtime",
            search_aliases=["dlss", "doctor", "setup", "dependency", "环境检测", "依赖检查"],
            description="Checks the selected NR/SR/Streamline runtime and host dependencies without launching a Worker/model. Passes runtime through unchanged. owned_sl requires NumPy and explicit renderer motion, not DIS/NVOF. Ready establishes files/host readiness, not compiled CSR1/CXR1 capability, valid renderer inputs or GPU acceptance.",
            inputs=[
                RuntimeConfig.Input("runtime", tooltip="Connect the exact Runtime Configuration used by NR Preview/Process or SR Render. owned_sr requires an SDK-enabled CSR1 Worker, nvngx_dlss.dll and a project UUID; no NR caller shim."),
                TemporalSequence.Input("sequence", optional=True,
                                       tooltip="Optional but recommended: checks the Input Adapter's media paths, selected guides and input readiness."),
                OpticalFlowProvider.Input("flow_provider", optional=True, advanced=True,
                                          tooltip="Use only when no Video Input Adapter is connected; checks this DIS/NVIDIA provider."),
                io.Boolean.Input("verify_nvidia_flow", default=False, optional=True, advanced=True,
                                 tooltip="When NVIDIA flow is selected, launch only our NVOF helper to query driver/GPU capability. Neither NR nor SR Workers/models are started; this does not test SR support."),
                io.Boolean.Input("include_diagnostics", default=False, optional=True, advanced=True,
                                 tooltip="Include fuller host command output. Paths and GPU details will appear in the copyable report."),
                MediaPipelineType.Input("pipeline", optional=True,
                                        tooltip="Connect Input Assembler or NR Stage to check resolved guide selection and media tools. Takes precedence over sequence/flow_provider; does not render or certify SR depth/metadata inputs."),
            ],
            outputs=[RuntimeConfig.Output("runtime"), io.Boolean.Output("ready"), io.String.Output("report")],
            is_output_node=True,
            is_experimental=True,
            not_idempotent=True,
        )

    @classmethod
    def execute(cls, runtime, sequence=None, flow_provider=None, verify_nvidia_flow=False, include_diagnostics=False, pipeline=None):
        if pipeline is not None:
            sequence = require_pipeline(pipeline).sequence_copy()
            flow_provider = None
        report = inspect_setup(runtime, sequence=sequence, flow_provider=flow_provider,
                               verify_nvidia_flow=verify_nvidia_flow, include_diagnostics=include_diagnostics)
        if pipeline is not None:
            report["input_pipeline"] = pipeline_report(pipeline)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        return io.NodeOutput(runtime, report["ready"], encoded, ui=diagnostic_ui("dlss_setup_report", report))
