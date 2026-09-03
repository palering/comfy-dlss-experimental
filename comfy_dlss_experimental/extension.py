from __future__ import annotations

from comfy_api.latest import ComfyExtension, io

from .nodes import (
    DLSSExperimentalDISFlow,
    DLSSExperimentalNVIDIAFlow,
    DLSSExperimentalCarrierTest,
    DLSSExperimentalNRProfile,
    DLSSExperimentalCompareVideo,
    DLSSExperimentalPrepareTemporalSequence,
    DLSSExperimentalPreviewSession,
    DLSSExperimentalProcessVideo,
    DLSSExperimentalRenderContract,
    DLSSExperimentalRuntimeConfig,
    DLSSExperimentalRuntimeProbe,
    DLSSExperimentalTemporalSettings,
)
from .preview_api import register_preview_routes


register_preview_routes()


class DLSSExperimentalExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            DLSSExperimentalDISFlow,
            DLSSExperimentalNVIDIAFlow,
            DLSSExperimentalRuntimeProbe,
            DLSSExperimentalRuntimeConfig,
            DLSSExperimentalCarrierTest,
            DLSSExperimentalNRProfile,
            DLSSExperimentalTemporalSettings,
            DLSSExperimentalPrepareTemporalSequence,
            DLSSExperimentalRenderContract,
            DLSSExperimentalPreviewSession,
            DLSSExperimentalProcessVideo,
            DLSSExperimentalCompareVideo,
        ]


async def comfy_entrypoint() -> DLSSExperimentalExtension:
    register_preview_routes()
    return DLSSExperimentalExtension()
