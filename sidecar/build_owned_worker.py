"""Build the owned NR acceptance host with existing Zig, without an MSVC runtime archive.

This is not a production video Worker package. Artifacts stay in sidecar/build.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def zig_lib_from_env(text: str) -> Path:
    """Accept old JSON and Zig 0.16 ZON output; never evaluate either as code."""
    match = re.search(r'^\s*(?:\.lib_dir\s*=|"lib_dir"\s*:)\s*("(?:[^"\\]|\\.)*")', text, re.MULTILINE)
    if not match:
        raise ValueError("Cannot find Zig lib_dir; pass --zig-lib-dir explicitly")
    return Path(json.loads(match.group(1)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngx-include", default=os.environ.get("NGX_SDK_INCLUDE"))
    parser.add_argument("--zig", default="zig")
    parser.add_argument("--zig-lib-dir", default=os.environ.get("ZIG_LIB_DIR"))
    parser.add_argument("--native-tests", action="store_true", help="Also run host ASan/UBSan parameter tests using CXX/clang++")
    args = parser.parse_args()
    if not args.ngx_include:
        parser.error("Provide --ngx-include pointing to official NVIDIA DLSS SDK headers")
    sdk = Path(args.ngx_include).resolve()
    for header in ("nvsdk_ngx.h", "nvsdk_ngx_defs.h", "nvsdk_ngx_params.h"):
        if not (sdk / header).is_file():
            parser.error(f"Missing official SDK header: {header}")
    root = Path(__file__).resolve().parent
    build = root / "build"
    environment = os.environ.copy()
    environment.setdefault("ZIG_GLOBAL_CACHE_DIR", str(build / "zig-cache-global"))
    environment.setdefault("ZIG_LOCAL_CACHE_DIR", str(build / "zig-cache-local"))
    lib = (Path(args.zig_lib_dir) if args.zig_lib_dir else zig_lib_from_env(
        subprocess.run([args.zig, "env"], check=True, capture_output=True, text=True, env=environment).stdout))
    lib = lib.resolve()
    headers = lib / "libc/include/any-windows-any"
    if not (headers / "wchar.h").is_file():
        parser.error("Selected Zig runtime has no bundled Windows C headers")
    build.mkdir(exist_ok=True)
    (build / "caller").mkdir(exist_ok=True)
    common = ["-std=c++20", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wsign-conversion",
              "-Werror", "-Wno-nullability-completeness", "-isystem", str(sdk), "-I", str(root / "src")]

    def run(command):
        subprocess.run(command, check=True, env=environment, cwd=root.parent)

    objects = []
    for source in (root / "src/nr_parameters.cpp", root / "tests/nr_parameter_abi_check.cpp"):
        target = build / (source.stem + "-ms.obj")
        run([args.zig, "c++", "-target", "x86_64-windows-msvc", *common,
             "-fno-rtti", "-fno-exceptions", "-nostdlib++", "-Wno-unused-command-line-argument",
             "-isystem", str(headers), "-c", str(source), "-o", str(target)])
        objects.append(str(target))
    run([args.zig, "c++", "-target", "x86_64-windows-gnu", *common, "-municode", "-DNGX_SNIPPET_BUILD",
         str(root / "src/nr_probe.cpp"), str(root / "src/nr_engine.cpp"), str(root / "src/nr_parameter_memory.cpp"),
         str(root / "src/sr_service.cpp"), *objects,
         "-o", str(build / "comfy-dlss-worker.exe"), "-luuid", "-lntdllcrt", "-lws2_32"])
    run([args.zig, "c++", "-target", "x86_64-windows-gnu", *common, "-DNGX_SNIPPET_BUILD",
         "-shared", "-fno-optimize-sibling-calls", "-fno-lto", "-fno-exceptions", "-fno-rtti", "-nostdlib++",
         str(root / "src/nr_caller.cpp"), "-o", str(build / "caller/nvngx.dll")])
    if args.native_tests:
        target = build / "nr-parameters-test"
        run([*shlex.split(os.environ.get("CXX", "clang++")), *common, "-O1", "-g",
             "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-pthread",
             str(root / "src/nr_parameters.cpp"), str(root / "tests/nr_parameter_test_memory.cpp"),
             str(root / "tests/nr_parameters_test.cpp"), str(root / "tests/nr_parameter_abi_check.cpp"), "-o", str(target)])
        run([str(target)])
        sequence_test = build / "nr-sequence-contract-test"
        run([*shlex.split(os.environ.get("CXX", "clang++")), *common, "-O1", "-g",
             "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
             str(root / "tests/nr_sequence_contract_test.cpp"), "-o", str(sequence_test)])
        run([str(sequence_test)])
        wire_test = build / "owned-wire-test"
        run([*shlex.split(os.environ.get("CXX", "clang++")), *common, "-O1", "-g",
             "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
             str(root / "tests/owned_wire_test.cpp"), "-o", str(wire_test)])
        run([str(wire_test)])
        sr_wire_test = build / "sr-wire-test"
        run([*shlex.split(os.environ.get("CXX", "clang++")), *common, "-O1", "-g",
             "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
             str(root / "tests/sr_wire_test.cpp"), "-o", str(sr_wire_test)])
        run([str(sr_wire_test)])
    print("Built owned NR development host/caller and optional tests. No model/GPU was run; existing runtime presets are unchanged.")


if __name__ == "__main__":
    main()
