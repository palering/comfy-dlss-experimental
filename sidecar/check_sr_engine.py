"""Compile-check the SDK-backed SR engine and run CPU-only contract tests.

No SDK archive is linked and no executable or GPU feature is advertised by this
check. Existing Zig plus an already-downloaded official SDK is sufficient.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngx-include", required=True)
    parser.add_argument("--zig", default="zig")
    args = parser.parse_args()
    sdk = Path(args.ngx_include).resolve()
    for header in ("nvsdk_ngx.h", "nvsdk_ngx_helpers.h", "nvsdk_ngx_defs.h", "nvsdk_ngx_params.h"):
        if not (sdk / header).is_file():
            parser.error(f"Missing official NGX SDK header: {header}")
    root = Path(__file__).resolve().parent
    build = root / "build"
    build.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.setdefault("ZIG_GLOBAL_CACHE_DIR", str(build / "zig-cache-global"))
    env.setdefault("ZIG_LOCAL_CACHE_DIR", str(build / "zig-cache-local"))
    common = ["-std=c++20", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wsign-conversion", "-Werror",
              "-Wno-nullability-completeness", "-I", str(root / "src")]
    subprocess.run([args.zig, "c++", "-target", "x86_64-windows-gnu", *common,
                    "-isystem", str(sdk), "-c", str(root / "src/sr_engine.cpp"),
                    "-o", str(build / "sr-engine-compile-check.obj")], check=True, env=env)
    test = build / "sr-input-contract-test"
    subprocess.run([*shlex.split(os.environ.get("CXX", "clang++")), *common,
                    "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    str(root / "tests/sr_input_contract_test.cpp"), "-o", str(test)], check=True, env=env)
    subprocess.run([str(test)], check=True, env=env)
    print("SR engine compile-check and CPU ASan/UBSan contracts passed; SDK linking and GPU acceptance were not performed.")


if __name__ == "__main__":
    main()
