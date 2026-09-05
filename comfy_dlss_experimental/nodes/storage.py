from __future__ import annotations

import json
from comfy_api.latest import io
from ..storage_manager import inventory


class DLSSExperimentalStorageManager(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalStorageManager", display_name="DLSS Storage Manager",
            category="DLSS Experimental/Diagnostics", search_aliases=["cache", "cleanup", "disk", "缓存", "清理"],
            description="Lists this instance's prepared caches and temporary DLSS results. Queueing only refreshes the list; deletion and saving shared limits require explicit card buttons. Save needed VIDEO outputs before deleting temporary jobs.",
            inputs=[
                io.Int.Input("cache_gib", default=2, min=1, max=1024, tooltip="Shared prepared-cache quota in GiB; inactive least-recently-used entries may be evicted."),
                io.Int.Input("temporary_gib", default=8, min=1, max=2048, tooltip="Total temporary-job quota in GiB. Old preview media is not automatically deleted; admission stops when full."),
                io.Int.Input("job_gib", default=2, min=1, max=1024, tooltip="Per-task temporary output budget in GiB; must not exceed the total temporary quota."),
                io.Int.Input("entry_mib", default=128, min=1, max=512, tooltip="Maximum raw prepared-input estimate per entry in MiB. Larger ranges stream without raw disk caches."),
                io.Int.Input("free_gib", default=2, min=1, max=1024, tooltip="Keep at least this much filesystem free space in GiB. Applies to rendering, not only retained caches."),
            ],
            outputs=[io.String.Output("report")], is_output_node=True, not_idempotent=True, is_experimental=True)

    @classmethod
    def execute(cls, **_kwargs):
        report = inventory()
        return io.NodeOutput(json.dumps(report, ensure_ascii=False, indent=2), ui={"dlss_storage": [report]})

    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        return float("nan")
