#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comfy_dlss_experimental.components import scan_component_manifests
from comfy_dlss_experimental.config import data_root


if __name__ == "__main__":
    root = data_root() / "components"
    components, errors = scan_component_manifests(root)
    print(json.dumps({"root": str(root), "components": components, "errors": errors}, ensure_ascii=False, indent=2))
