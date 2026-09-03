"""Comfy VIDEO inspection without materializing a complete video."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from .input_policy import InputColorPolicy, analyze_media
from .media_clip import inspect_media


def inspect_video_input(video, policy: InputColorPolicy, guide_mode: str, cancelled=None) -> dict:
    from comfy_api.latest import InputImpl
    public = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "policy": asdict(policy), "guide_mode": guide_mode, "guide_state": "deferred_to_consumer",
              "issues": [], "warnings": [], "assumptions": [], "plan": [], "ready": False,
              "state": "unreadable", "header_readable": False, "runtime_status": "not_probed",
              "timing_validation": "not_run", "decode_validation": "not_run"}
    try:
        width, height = video.get_dimensions()
        public["active_view"] = {"width": width, "height": height, "duration": float(video.get_duration())}
        if isinstance(video, InputImpl.VideoFromFile):
            source = video.get_stream_source()
            if isinstance(source, (str, Path)):
                source = Path(source).resolve(strict=True)
                public["filename"] = source.name
                report = analyze_media(inspect_media(source, cancelled=cancelled), policy)
                public.update(report)
                public.pop("resolved_video", None)
                start, duration = video.get_active_trim_window()
                public["active_view"].update(trim_start=start, trim_duration=duration)
                raw = report["source_video"] or {}
                cropped = (width, height) != (raw.get("width"), raw.get("height"))
                public["active_view"]["cropped"] = cropped
                if cropped:
                    public["plan"].insert(0, "保留上游裁剪，仅物化选定区间；物化后再次检查媒体。")
                    if report["assumptions"]:
                        public["ready"] = False
                        public["state"] = "unsupported"
                        public["issues"].append({"kind": "unsupported", "message": "带上游裁剪且缺失色彩标签的 VIDEO 暂不支持直接补全；请先对原文件进行输入适配。"})
                public["plan"].append({
                    "dis": "运动矢量：DIS 光流估计。",
                    "nvidia": "运动矢量：Linux 原生 NVIDIA 硬件光流；所选区间处理时验证能力，不经 Proton。",
                    "zero": "运动矢量：全零；仍检测镜头切换并保留连续历史，不关闭 NR。",
                }.get(guide_mode, "运动矢量：未知提供器，执行前需要验证。"))
                return public
        # Public generic VIDEO interfaces do not expose cheap reliable headers.
        # Do not decode a whole tensor/video just to populate an information card.
        public.update(state="deferred", ready=True, filename="内存或流式 VIDEO",
                      warnings=["此 VIDEO 不提供可直接检查的本地文件；选定区间物化后才能检查编码、色彩和时间戳。"],
                      plan=["保留上游编辑，只物化请求区间。", "物化后按所选色彩策略检查，再准备运动矢量。"])
        return public
    except InterruptedError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        public.update(state="unreadable", ready=False)
        public["issues"].append({"kind": "unreadable", "message": str(exc)})
        return public
