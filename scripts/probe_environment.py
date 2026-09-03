#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comfy_dlss_experimental.probe import probe_environment


if __name__ == "__main__":
    print(json.dumps(probe_environment(include_diagnostics=True).to_dict(), ensure_ascii=False, indent=2))
