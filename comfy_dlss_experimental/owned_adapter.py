"""Explicit RGBA8 media boundary for the CNR1 FP16 Worker.

This adapter preserves the existing SDR media/stack contract. Normalization is
numeric /255, NOT an implicit sRGB EOTF. Output is finite-checked, clamped and
quantized back to RGBA8 per pass. It is not an HDR or float-stack implementation.
"""
from dataclasses import asdict

from .execution_log import phase
from .owned_worker import OwnedNRSettings


def owned_settings(settings):
    settings.validate()
    if settings.frame_count + settings.warmup > 1_000_000:
        raise ValueError("owned_nr evaluation limit includes warmup; shorten this task")
    if settings.profile != 1:
        raise ValueError("owned_nr does not implement external Worker profiles; choose Current compatible configuration (1).")
    values = asdict(settings)
    values.pop("frame_count")
    values.pop("profile")
    result = OwnedNRSettings(**values)
    result.encode()
    return result


def rgba8_to_half(color):
    import numpy as np
    return (np.frombuffer(color, np.uint8).astype(np.float32) / 255).astype('<f2').tobytes()


def half_to_rgba8(color, expected_bytes):
    import numpy as np
    if len(color) != expected_bytes * 2:
        raise ValueError("Owned NR output has the wrong RGBA16F length")
    values = np.frombuffer(color, '<f2').astype(np.float32)
    if not np.isfinite(values).all():
        raise ValueError("Owned NR output contains non-finite values")
    return np.rint(values.clip(0, 1) * 255).astype(np.uint8).tobytes()


class OwnedMediaClient:
    """Media executor interface; the actual wire client remains format-specific."""
    protocol = "CNR1"

    def __init__(self, session, settings):
        native = owned_settings(settings)
        self.session, self.settings = session, settings
        self.index = 0
        self.failed = False
        with phase("worker_session_create"):
            session.client.create(native, session_id=1)

    def process(self, color, motion, pts_ns, *, reset=False):
        if self.failed or self.index >= self.settings.frame_count:
            raise RuntimeError("Owned media stream failed or exhausted")
        if len(color) != self.settings.plane_bytes or len(motion) != self.settings.plane_bytes:
            raise ValueError("Expected packed RGBA8 color and RG16F motion")
        try:
            with phase("owned_color_conversion"):
                half = rgba8_to_half(color)
            result = self.session.client.process(half, motion, pts_ns, reset=reset)
            with phase("owned_color_conversion"):
                output = half_to_rgba8(result, len(color))
            self.index += 1
            return output
        except BaseException:
            self.failed = True
            raise

    def end_task(self):
        try:
            self.session.client.end()
        except BaseException:
            self.failed = True
            raise

    def finish(self):
        if self.failed or self.index != self.settings.frame_count:
            raise RuntimeError("Cannot finish an incomplete owned media task")
        self.end_task()
        self.session.shutdown()
        return {"protocol": "CNR1", "shutdown_acknowledged": True}


def launch_owned(runtime, caller, worker, job, root, proton, environment, suffix=""):
    from .owned_process import OwnedWorkerProcess
    return OwnedWorkerProcess(worker, worker.parent / "nvngx_dlssnr.dll", caller, job,
        proton=proton,
        compatdata=(root / "prefixes" / ("owned-" + runtime["runtime_key"][:24] + suffix)) if proton else None,
        environment=environment, timeout=120)


def media_contract(runtime):
    owned = runtime.get("backend") == "owned_nr"
    return {"backend_id": "owned_cnr1_nr" if owned else "external_d5v2_nr",
            "protocol": "CNR1" if owned else "D5V2",
            "media_color": "rgba8", "worker_color": "rgba16f_le" if owned else "rgba8",
            "implicit_eotf": False, "inter_pass_color": "rgba8",
            "float_output_quantized": owned}
