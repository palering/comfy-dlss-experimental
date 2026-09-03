"""ComfyUI entrypoint for comfy-dlss-experimental."""

from .comfy_dlss_experimental.extension import comfy_entrypoint

WEB_DIRECTORY = "./web"

__all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
