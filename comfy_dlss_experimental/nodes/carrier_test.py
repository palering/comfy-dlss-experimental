from __future__ import annotations

import json

from comfy_api.latest import io

from ..config import data_root
from ..jobs import run_carrier_bootstrap
from .types import RuntimeConfig


class DLSSExperimentalCarrierTest(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalCarrierTest",
            display_name="DLSS Carrier Bootstrap Test",
            category="DLSS Experimental/Runtime",
            search_aliases=["dlss carrier", "reshade test", "renodx test", "proton test"],
            description=(
                "Stages the selected runtime in an isolated job and verifies the real "
                "Proton/ReShade/RenoDX/D3D12 carrier chain. It does not evaluate DLSS frames yet."
            ),
            inputs=[
                RuntimeConfig.Input("runtime"),
                io.Int.Input("present_count", default=180, min=30, max=600, step=30, advanced=True),
            ],
            outputs=[RuntimeConfig.Output("verified_runtime"), io.String.Output("report")],
            is_output_node=True,
            not_idempotent=True,
            is_experimental=True,
        )

    @classmethod
    def execute(cls, runtime: dict, present_count: int) -> io.NodeOutput:
        if not runtime.get("ready"):
            return io.NodeOutput(block_execution="DLSS runtime is not ready. Inspect Runtime Configuration first.")
        try:
            result = run_carrier_bootstrap(
                runtime=runtime,
                data_root=data_root(),
                presents=int(present_count),
            )
        except (OSError, ValueError, KeyError) as exc:
            result = {"schema_version": 1, "ok": False, "stage": "node", "error": str(exc)}

        verified = dict(runtime)
        verified["carrier_verified"] = bool(result.get("ok"))
        verified["carrier_result"] = result
        if not result.get("ok"):
            verified["ready"] = False
            errors = list(verified.get("errors") or [])
            errors.append(f"Carrier bootstrap failed at {result.get('stage')}: {result.get('error')}")
            verified["errors"] = errors
        return io.NodeOutput(verified, json.dumps(result, ensure_ascii=False, indent=2))
