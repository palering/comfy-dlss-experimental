"""Pure, non-mutating media preflight. Assumptions are explicit and recorded."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from fractions import Fraction
import math
from .color_normalization import color_plan

COLOR_FIELDS = ("color_space", "color_primaries", "color_transfer", "color_range")
MISSING = (None, "", "unknown", "unspecified", "N/A")


@dataclass(frozen=True)
class InputColorPolicy:
    mode: str = "strict"
    transfer: str = "bt709"
    range: str = "tv"
    normalize_to_srgb: bool = False

    def validate(self):
        if self.mode not in ("strict", "fill_missing"):
            raise ValueError("Unknown input color policy")
        if self.transfer not in ("bt709", "iec61966-2-1") or self.range not in ("tv", "pc"):
            raise ValueError("Unsupported assumed transfer/range")
        if type(self.normalize_to_srgb) is not bool:
            raise ValueError("normalize_to_srgb must be boolean")

    @classmethod
    def from_dict(cls, value=None):
        result = cls(**(value or {}))
        result.validate()
        return result


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def analyze_media(raw: dict, policy: InputColorPolicy = InputColorPolicy()) -> dict:
    """Header-level eligibility, NOT a guarantee that all frames decode or are CFR."""
    policy.validate()
    original = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = [deepcopy(s) for s in raw.get("streams", []) if s.get("codec_type") == "audio"]
    report = {"schema_version": 1, "state": "unsupported", "ready": False,
              "source_video": deepcopy(original), "format": deepcopy(raw.get("format", {})),
              "audio": audio, "effective_color": {}, "assumptions": [], "issues": [], "warnings": [],
              "policy": asdict(policy), "header_readable": True, "decode_validation": "not_run",
              "timing_validation": "selected_range_checked_during_preparation",
              "plan": [], "runtime_status": "not_probed", "fps": None,
              "color_normalization": color_plan(original or {}, policy.normalize_to_srgb, ready=False)}
    if original is None:
        report["issues"].append({"kind": "unsupported", "message": "输入不包含视频流。"})
        return report
    video = deepcopy(original)
    assumed = {"color_space": "bt709", "color_primaries": "bt709",
               "color_transfer": policy.transfer, "color_range": policy.range}
    supported = {"color_space": ("bt709",), "color_primaries": ("bt709",),
                 "color_transfer": ("bt709", "iec61966-2-1"), "color_range": ("tv", "pc")}
    for field in COLOR_FIELDS:
        value = video.get(field)
        if value in MISSING:
            if policy.mode == "fill_missing":
                video[field] = assumed[field]
                report["assumptions"].append({"field": field, "original": value, "interpreted_as": assumed[field]})
            else:
                report["issues"].append({"kind": "needs_confirmation", "field": field,
                    "message": f"缺少 {field}。请在视频输入适配中选择 fill_missing，并指定传递曲线和范围。"})
        elif value not in supported[field]:
            report["issues"].append({"kind": "unsupported", "field": field,
                "message": f"当前 NR 输入不支持 {field}={value}；补全模式不会覆盖已知标签，也不会执行 HDR 色调映射。"})
    report["effective_color"] = {field: video.get(field) for field in COLOR_FIELDS}
    if any("Mastering display" in s.get("side_data_type", "") or "Content light" in s.get("side_data_type", "")
           or "DOVI" in s.get("side_data_type", "") or "HDR" in s.get("side_data_type", "")
           for s in video.get("side_data_list", [])):
        report["issues"].append({"kind": "unsupported", "message": "检测到 HDR 辅助元数据，当前版本不执行 HDR→SDR。"})
    if video.get("sample_aspect_ratio", "1:1") not in ("1:1", "0:1", "N/A"):
        report["issues"].append({"kind": "unsupported", "message": "非方形像素需要先规范化；当前不会自动拉伸。"})
    if any((finite_number(side.get("rotation", 0)) or 0) % 360 for side in video.get("side_data_list", [])):
        report["issues"].append({"kind": "unsupported", "message": "视频带旋转信息，需要先规范化；当前不会忽略旋转。"})
    try:
        rate = Fraction(video.get("avg_frame_rate", "0/0"))
        if not 1 <= rate <= 120:
            raise ValueError()
        report["fps"] = str(rate)
    except (ValueError, ZeroDivisionError, TypeError):
        report["issues"].append({"kind": "unsupported", "message": "帧率未知或不在 1–120 fps 范围内。"})
    for name in ("width", "height"):
        size = video.get(name)
        if type(size) is not int or size < 64:
            report["issues"].append({"kind": "unsupported", "message": f"不支持的画面尺寸：{name}={size}。"})
    if video.get("r_frame_rate") != video.get("avg_frame_rate"):
        report["warnings"].append("标称帧率与平均帧率不同或未知；是否为 CFR 将在所选区间解码时验证。")
    if any(type(video.get(k)) is int and video[k] % 2 for k in ("width", "height")):
        report["warnings"].append("准备时目标尺寸会向下对齐到偶数；以预览/处理结果中的实际尺寸为准。")
    if (video.get("width", 0) or 0) > 7680 or (video.get("height", 0) or 0) > 4320:
        report["warnings"].append("原始尺寸超过当前 Worker 上限，需在预览/处理节点选择适当缩放。")
    video_start = finite_number(video.get("start_time"))
    for track in audio:
        start = finite_number(track.get("start_time"))
        if start is not None and video_start is not None and abs(start - video_start) > 0.001:
            report["issues"].append({"kind": "unsupported", "message": "音视频起始时间不一致；精确偏移处理尚未实现，当前阻止可能失步的输出。"})
    report["plan"] = ["不修改源文件，不预转码整段视频。", "仅解码请求区间及其前置帧。",
                      "按有效矩阵/范围转为非线性 RGBA8 全范围；不冒充传递曲线转换。",
                      "准备时检查时间戳连续性、实际尺寸、帧数与可用磁盘；不再限制为 30 秒或 2 GiB。长视频缓存仍占用磁盘。",
                      "不运行 NR；编码与最终文件设置留在输出端。"]
    if report["assumptions"]:
        report["warnings"].append("有效色彩包含用户指定的假设，不代表恢复了原始色彩标签。")
    kinds = {issue["kind"] for issue in report["issues"]}
    report["state"] = ("unsupported" if "unsupported" in kinds else "needs_confirmation" if kinds
                       else "ready_with_assumptions" if report["assumptions"] else "ready")
    report["ready"] = not kinds
    normalization = color_plan(video, policy.normalize_to_srgb, ready=report["ready"])
    report["color_normalization"] = normalization
    if normalization["operation"] == "bt709_to_srgb":
        report["plan"][2] = "仅选定区间：解码为全范围 RGBA8，实际将 BT.709 传递曲线转换为 sRGB，再构建光流并运行 NR。"
        report["plan"].insert(3, "对照 A 与结果 B 共用 sRGB 工作空间；导出前实际转回 BT.709 传递曲线。")
        report["warnings"].append("sRGB 归一化是可选实验：采用标称传递曲线转换，不保证 NR 效果更好；RGBA8 转换有量化损失，不是无损色彩往返。")
    elif normalization["operation"] == "already_srgb":
        report["plan"][2] = "源传递曲线已经是 sRGB：只进行原有矩阵/范围解码，不重复转换传递曲线。"
    report["resolved_video"] = video
    return report


def validated_metadata(raw: dict, policy: InputColorPolicy = InputColorPolicy()) -> dict:
    report = analyze_media(raw, policy)
    if not report["ready"]:
        raise ValueError("视频输入适配未通过：" + "；".join(i["message"] for i in report["issues"]))
    return {"video": report["resolved_video"], "fps": report["fps"], "has_audio": bool(report["audio"]),
            "input_report": report}
