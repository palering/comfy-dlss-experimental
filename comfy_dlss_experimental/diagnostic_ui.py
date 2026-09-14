"""Keep structured diagnostics out of Comfy's media-descriptor namespace."""
import json


def diagnostic_ui(channel: str, report: dict) -> dict[str, list[str]]:
    # Jobs inspects every dict in a UI list as media, including unknown channels.
    # A report's nested `format` is not a MIME type; retain it inside JSON text.
    # Internal objects and STRING sockets retain their existing contracts.
    return {channel: [json.dumps(report, ensure_ascii=False, indent=2)]}
