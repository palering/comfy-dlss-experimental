from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    from .execution_context import current_execution_context
    context = current_execution_context()
    if context is not None:
        return context.data_root
    override = os.environ.get("COMFY_DLSS_HOME")
    if override:
        return Path(override).expanduser()

    try:
        import folder_paths  # type: ignore

        user_root = Path(folder_paths.get_user_directory())
    except (ImportError, AttributeError):
        user_root = Path.cwd() / "user"
    if user_root.name != "default":
        user_root = user_root / "default"
    return user_root / "comfy-dlss-experimental"
