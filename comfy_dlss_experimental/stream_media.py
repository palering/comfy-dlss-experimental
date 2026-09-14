"""Frame-at-a-time input and pipe encoding. No whole-clip pixel arrays or raw files."""
from __future__ import annotations

from collections import deque
from contextvars import copy_context
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

from .color_normalization import convert_rgba8, export_transfer_filter
from .execution_log import phase
from .media_clip import file_hash, probe_media, run_cancellable, validate_cfr
from .media_tools import media_executable
from .storage_budget import frame_budget
from .storage_manager import check_job, file_allowance
from .temporal_guides import TemporalGuideGenerator


def decoded(source, request, metadata, cancelled):
    import av
    start = Fraction(str(max(0, request.start - request.pre_roll)))
    end = Fraction(str(request.start)) + Fraction(str(request.duration))
    with av.open(str(source)) as container:
        stream = next(s for s in container.streams.video if s.index == metadata["video"]["index"])
        origin = Fraction(stream.start_time or 0) * stream.time_base
        if start > 0:
            container.seek(int((origin + start) / stream.time_base), stream=stream, backward=True)
        for frame in container.decode(stream):
            if cancelled():
                raise InterruptedError("DLSS input decoding cancelled")
            if frame.pts is None:
                raise ValueError("source frame has no timestamp")
            relative = Fraction(frame.pts) * frame.time_base - origin
            if relative < start:
                continue
            if relative >= end:
                break
            if (frame.width, frame.height) != (metadata["video"]["width"], metadata["video"]["height"]):
                raise ValueError("midstream resolution changes are not supported")
            yield frame, round(relative * 1_000_000_000), relative >= Fraction(str(request.start))
            if request.single_frame and relative >= Fraction(str(request.start)):
                break


def inspect_stream(source, request, guides, policy, cancelled, progress):
    """The external header needs an exact frame count: scan timing, discard pixels.

    This first decode validates timing without computing optical flow or writing
    raw data. Only small frame descriptors survive for the subsequent stream.
    """
    metadata = probe_media(source, cancelled=cancelled, color_policy=policy)
    width, height = (int(metadata["video"][k] * request.scale) // 2 * 2 for k in ("width", "height"))
    if min(width, height) < 64 or width > 7680 or height > 4320:
        raise ValueError("unsupported clip dimensions")
    rate = Fraction(metadata["fps"])
    limit = frame_budget(request.duration, request.pre_roll, rate, request.single_frame)
    frames = []
    with phase("input_timing_scan"):
        for _, pts, visible in decoded(source, request, metadata, cancelled):
            if len(frames) >= limit:
                raise ValueError("frame count exceeded input budget")
            frames.append({"pts_ns": pts, "visible": visible})
            if len(frames) % 64 == 0:
                progress("input_timing_scan", len(frames), limit)
    validate_cfr([f["pts_ns"] for f in frames], rate)
    visible_start = next((i for i, f in enumerate(frames) if f["visible"]), None)
    if visible_start is None:
        raise ValueError("no visible frame after pre-roll")
    report = metadata["input_report"]
    report.update(decode_validation="selected_range_passed", timing_validation="selected_range_cfr_passed",
                  validated_request=asdict(request))
    report["color_normalization"]["execution"] = "stream_on_demand"
    return {"schema_version": 1, "streaming": True, "source": str(source), "source_sha256": file_hash(source),
            "request": asdict(request), "guide_settings": asdict(guides), "metadata": metadata,
            "width": width, "height": height, "fps": str(rate), "frames": frames,
            "visible_start_index": visible_start, "visible_count": len(frames) - visible_start,
            "color_pipeline": report["color_normalization"],
            "storage_plan": {"mode": "streaming", "raw_disk_bytes": 0, "queued_frames": 1,
                             "note": "Decode twice for exact timing; color/flow/NR/output stream one frame at a time. Model/codec working memory is additional."}}


def prepared_frames(manifest, guides, cancelled):
    from av.video.reformatter import VideoReformatter
    from .media_clip import ClipRequest
    source = Path(manifest["source"])
    if guides.motion_provider == "external" and file_hash(source) != manifest["source_sha256"]:
        raise ValueError("Input source content changed after timing scan; external guides cannot be reused")
    request = ClipRequest(**manifest["request"])
    width, height = manifest["width"], manifest["height"]
    pipeline = manifest["color_pipeline"]
    reformatter = VideoReformatter()
    index = 0
    source_identity = {"sha256": manifest["source_sha256"],
                       "width": manifest["metadata"]["video"]["width"],
                       "height": manifest["metadata"]["video"]["height"]}
    with TemporalGuideGenerator(width, height, guides, cancelled=cancelled, source_identity=source_identity) as temporal:
        for frame, pts, visible in decoded(source, request, manifest["metadata"], cancelled):
            if index >= len(manifest["frames"]) or {"pts_ns": pts, "visible": visible} != manifest["frames"][index]:
                raise ValueError("Input timing changed between scan and render")
            with phase("color_conversion"):
                color = reformatter.reformat(frame, width=width, height=height, format="rgba",
                    src_colorspace="ITU709", dst_colorspace="ITU709",
                    src_color_range="MPEG" if manifest["metadata"]["video"]["color_range"] == "tv" else "JPEG",
                    dst_color_range="JPEG").to_ndarray().tobytes()
                if pipeline["operation"] == "bt709_to_srgb":
                    color = convert_rgba8(color, pipeline["source_transfer"], pipeline["working_transfer"])
            with phase("optical_flow"):
                motion, info = temporal.process(color, pts_ns=pts)
            yield color, motion, {"pts_ns": pts, "visible": visible, **info}
            index += 1
    if index != len(manifest["frames"]):
        raise ValueError("Input frame count changed between scan and render")


class StreamEncoder:
    """One bounded input frame; ffmpeg backpressure controls decode/NR speed.

    A watchdog interrupts blocked pipe writes on cancellation/timeout. stderr is
    drained into a bounded tail. -fs caps each file; frame-count verification
    rejects truncation instead of returning a partial video as success.
    """
    def __init__(self, destination, manifest, cancelled):
        self.destination = Path(destination)
        self.partial = self.destination.with_suffix(".partial.mp4")
        self.manifest, self.cancelled = manifest, cancelled
        self.count = 0
        self.errors = deque(maxlen=8)
        self.stop = threading.Event()
        self.abort_reason = None
        self.published = False
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        if self.destination.exists() or self.partial.exists():
            raise FileExistsError(self.destination)
        width, height = manifest["width"], manifest["height"]
        self.plane = width * height * 4
        rate = Fraction(manifest["fps"])
        duration = float(manifest["visible_count"] / rate)
        first = manifest["frames"][manifest["visible_start_index"]]["pts_ns"] / 1e9
        audio = manifest["metadata"]["has_audio"]
        transfer = manifest["metadata"]["video"]["color_transfer"]
        self.deadline = time.monotonic() + max(120, min(86400, duration * 100 + 120))
        color_filter = export_transfer_filter(manifest.get("color_pipeline")) + (
            "scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p,"
            f"setparams=range=limited:color_primaries=bt709:color_trc={transfer}:colorspace=bt709")
        allowance = file_allowance()
        if allowance < 1024 * 1024:
            raise ValueError("No remaining temporary output budget")
        command = [media_executable("ffmpeg"), "-hide_banner", "-v", "error", "-n", "-f", "rawvideo",
                   "-pixel_format", "rgba", "-video_size", f"{width}x{height}", "-framerate", str(rate), "-i", "pipe:0"]
        if audio:
            command += ["-ss", f"{first:.9f}", "-i", manifest["source"]]
        command += ["-map", "0:v:0"]
        if audio:
            # Trim decoded samples before AAC framing; -t alone can retain a
            # full final audio frame beyond a short visible video interval.
            # Preserve timestamps after -ss, including any source audio offset.
            command += ["-map", "1:a:0", "-af", f"atrim=end={duration:.9f}", "-c:a", "aac", "-b:a", "160k"]
        command += ["-vf", color_filter, "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-threads", "4",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", transfer,
                    "-t", f"{duration:.9f}", "-fs", str(allowance), "-map_metadata", "-1", "-movflags", "+faststart", str(self.partial)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.reader = threading.Thread(target=self._drain, daemon=True)
        context = copy_context()
        self.watcher = threading.Thread(target=lambda: context.run(self._watch), daemon=True)
        self.reader.start()
        self.watcher.start()

    def _drain(self):
        while chunk := self.process.stderr.read(1024):
            self.errors.append(chunk)

    def _watch(self):
        while not self.stop.wait(.2):
            try:
                check_job()
            except (OSError, ValueError) as exc:
                self.abort_reason = str(exc)
                if self.process.poll() is None:
                    self.process.kill()
                return
            if self.cancelled() or time.monotonic() > self.deadline:
                self.abort_reason = "cancelled" if self.cancelled() else "encoder timeout"
                if self.process.poll() is None:
                    self.process.kill()
                return

    def write(self, color):
        if len(color) != self.plane:
            raise ValueError("Encoder input frame size mismatch")
        if self.count % 16 == 0:
            check_job()
        if self.cancelled():
            raise InterruptedError("Encoding cancelled")
        try:
            self.process.stdin.write(color)
        except (BrokenPipeError, OSError) as exc:
            if self.cancelled():
                raise InterruptedError("Encoding cancelled") from exc
            raise RuntimeError("Encoder stopped (output budget, timeout, or codec error): " +
                               b"".join(self.errors).decode(errors="replace")) from exc
        self.count += 1

    def finish(self):
        self.process.stdin.close()
        while self.process.poll() is None:
            if self.cancelled():
                raise InterruptedError("Encoding cancelled")
            time.sleep(.05)
        self.reader.join(timeout=2)
        if self.process.returncode or self.abort_reason:
            raise RuntimeError(self.abort_reason or b"".join(self.errors).decode(errors="replace"))
        manifest = self.manifest
        if self.count != manifest["visible_count"]:
            raise ValueError("Encoder did not receive every visible frame")
        verify = run_cancellable([media_executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames", "-of", "json", str(self.partial)],
            timeout=max(30, self.count / float(Fraction(manifest["fps"])) * 2 + 30), cancelled=self.cancelled)
        if int(json.loads(verify.stdout)["streams"][0]["nb_read_frames"]) != self.count:
            raise ValueError("Output was truncated, possibly by the storage limit; no partial video is published")
        encoded = probe_media(self.partial, cancelled=self.cancelled)
        if (encoded["video"]["color_transfer"] != manifest["metadata"]["video"]["color_transfer"] or
                encoded["video"]["color_range"] != "tv" or encoded["fps"] != manifest["fps"] or
                encoded["has_audio"] != manifest["metadata"]["has_audio"]):
            raise ValueError("Encoder changed color, timing or audio contract")
        check_job()
        self.partial.rename(self.destination)
        self.published = True
        return self.destination

    def close(self):
        self.stop.set()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.watcher.join(timeout=2)
        self.reader.join(timeout=2)
        for file in (self.process.stdin, self.process.stderr):
            try:
                file.close()
            except OSError:
                pass
        if not self.published:
            self.partial.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
