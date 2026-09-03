"""Host media executables, scoped to one job; never mutate process PATH."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess

_active: ContextVar[dict | None] = ContextVar("dlss_media_tools", default=None)


def _candidate(name: str, configured: str, companion: Path | None = None) -> tuple[Path, str]:
    if not isinstance(configured, str) or "\x00" in configured:
        raise ValueError(f"{name} path must be a local path string")
    filename = name + (".exe" if os.name == "nt" else "")
    if configured.strip():
        path = Path(configured.strip()).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{name}: use an absolute executable or directory path, without command arguments")
        return (path / filename if path.is_dir() else path), "explicit"
    if companion is not None:
        return companion / filename, "ffmpeg_directory"
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} was not found on the Comfy backend PATH; configure its path in Video Input Adapter")
    return Path(found), "PATH"


@lru_cache(maxsize=32)
def _version(name: str, path: str, size: int, modified_ns: int) -> str:
    # Cache by file identity; no repeated -version subprocess for Look changes.
    result = subprocess.run([path, "-version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=5, check=True)
    first = result.stdout.splitlines()[0] if result.stdout else ""
    if not first.lower().startswith(name + " version "):
        raise ValueError(f"Selected {name} executable did not identify itself as {name}")
    return first[:500]


def resolve_media_tools(config: dict | None = None) -> dict:
    config = {} if config is None else config
    if not isinstance(config, dict) or set(config) - {"ffmpeg_path", "ffprobe_path"}:
        raise ValueError("Invalid media tool configuration")
    config = {key: value.strip() if isinstance(value, str) else value
              for key, value in config.items()}
    report = {"provider": "host_executables", "ready": True}
    companion = None
    for name in ("ffmpeg", "ffprobe"):
        item = {"available": False, "path": None, "source": "explicit" if config.get(name + "_path") else "PATH"}
        try:
            if name == "ffprobe" and config.get("ffmpeg_path") and companion is None and not config.get("ffprobe_path"):
                item["source"] = "ffmpeg_directory"
                raise ValueError("Resolve the explicit ffmpeg path first, or configure ffprobe separately")
            path, source = _candidate(name, config.get(name + "_path", ""), companion if name == "ffprobe" else None)
            # Use the selected directory for the companion before resolving a
            # symlink; packaged ffmpeg/ffprobe may themselves be symlinks.
            if name == "ffmpeg" and config.get("ffmpeg_path", "").strip():
                companion = path.parent
            item.update(path=str(path), source=source)
            path = path.resolve(strict=True)
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError(f"{name} is not an executable file: {path}")
            info = path.stat()
            item.update(path=str(path), size=info.st_size, modified_ns=info.st_mtime_ns)
            item["version"] = _version(name, str(path), info.st_size, info.st_mtime_ns)
            item["available"] = True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            item["error"] = str(exc)
            report["ready"] = False
        report[name] = item
    try:
        report["pyav_version"] = importlib.metadata.version("av")
    except importlib.metadata.PackageNotFoundError:
        report["pyav_version"] = None
    return report


def media_executable(name: str) -> str:
    tools = _active.get()
    return tools[name]["path"] if tools is not None else name


def media_tool_identity() -> dict | None:
    tools = _active.get()
    return tools


@contextmanager
def media_tools_scope(config=None):
    """Resolve once per synchronous job; concurrent workflows remain isolated."""
    report = resolve_media_tools(config)
    from .execution_log import current_trace
    trace = current_trace()
    if trace:
        trace.record["media_tools"] = report
    if not report["ready"]:
        raise ValueError("Media tools unavailable: " + "; ".join(
            report[name]["error"] for name in ("ffmpeg", "ffprobe") if not report[name]["available"]))
    token = _active.set(report)
    try:
        yield report
    finally:
        _active.reset(token)


def with_media_tools(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with media_tools_scope(kwargs["sequence"].get("media_tools_config")):
            return function(*args, **kwargs)
    return wrapped
