"""Numerical guide providers do not execute models or read complete videos."""
import json
import hashlib
from pathlib import Path

from comfy_api.latest import io

from ..external_guides import MAX_MANIFEST_BYTES, load_external_guide, select_external_guide
from .types import TemporalSequence

ExternalGuideType = io.Custom("DLSS_GUIDE_PROVIDER")


class DLSSExperimentalExternalGuide(io.ComfyNode):
    @classmethod
    def fingerprint_inputs(cls, manifest_path, **kwargs):
        """Refresh metadata when a selected manifest changes, never frame files."""
        path = Path(manifest_path).expanduser().resolve()
        try:
            with path.open("rb") as handle:
                content = handle.read(MAX_MANIFEST_BYTES + 1)
        except OSError:
            return float("nan")  # execute supplies the actionable filesystem error.
        if len(content) > MAX_MANIFEST_BYTES:
            raise ValueError("External guide manifest exceeds 8 MiB")
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalExternalGuide", display_name="DLSS External Numerical Guide",
            category="DLSS Experimental/Guides/External",
            description="Load a sidecar manifest for numerical motion/depth/confidence/normals/masks. Binds the active VIDEO view. Only requested frames are read and content/timestamp checked. RGB visualizations are not accepted; this does not run an estimator or imply backend consumption.",
            inputs=[TemporalSequence.Input("sequence"),
                    io.String.Input("manifest_path", default="", tooltip="Path on the ComfyUI host to a schema-v1 numerical-guide JSON manifest; raw/.npy files are relative to this manifest.")],
            outputs=[ExternalGuideType.Output("guide"), io.String.Output("report")],
            is_experimental=True)

    @classmethod
    def execute(cls, sequence, manifest_path):
        if (not isinstance(sequence, dict) or type(sequence.get("schema_version")) is not int
                or sequence.get("schema_version") != 2 or sequence.get("video") is None):
            raise ValueError("Connect the current DLSS Video Input Adapter sequence")
        public = sequence.get("public", {})
        if not isinstance(public, dict):
            raise ValueError("Input sequence metadata must be an object")
        provider = load_external_guide(manifest_path, source_video=sequence["video"],
                                       source_width=public.get("width"), source_height=public.get("height"))
        return io.NodeOutput(provider, json.dumps(provider.report(), ensure_ascii=False, indent=2))


class DLSSExperimentalGuideSelector(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalGuideSelector", display_name="DLSS External Guide Selector",
            category="DLSS Experimental/Guides/External",
            description="Select one external numerical-guide provider before frame reading. Only the selected lazy branch is requested. Missing selection fails without fallback.",
            inputs=[io.Combo.Input("selection", options=["a", "b", "c"], default="a"),
                    ExternalGuideType.Input("a", optional=True, lazy=True),
                    ExternalGuideType.Input("b", optional=True, lazy=True),
                    ExternalGuideType.Input("c", optional=True, lazy=True)],
            outputs=[ExternalGuideType.Output("guide"), io.String.Output("report")], is_experimental=True)

    @classmethod
    def check_lazy_status(cls, selection="a", a=None, b=None, c=None):
        if selection not in ("a", "b", "c"):
            raise ValueError("Choose guide input a, b or c")
        return [selection] if {"a": a, "b": b, "c": c}[selection] is None else []

    @classmethod
    def execute(cls, selection="a", a=None, b=None, c=None):
        provider = select_external_guide(selection, a, b, c)
        return io.NodeOutput(provider, json.dumps({"selected": selection, **provider.report()}, ensure_ascii=False, indent=2))
