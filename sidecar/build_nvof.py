"""Maintainer build: existing Zig + CUDA headers, no SDK installation/download."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["linux-x86_64", "windows-x86_64"], default="linux-x86_64")
    parser.add_argument("--cuda-include", default=os.environ.get("NVOF_CUDA_INCLUDE", "/opt/cuda/targets/x86_64-linux/include"))
    args = parser.parse_args()
    include = Path(args.cuda_include)
    if not (include / "cuda.h").is_file():
        parser.error("Pass --cuda-include pointing to existing NVIDIA CUDA headers (cuda.h)")
    root = Path(__file__).resolve().parent
    output = root / "build"
    output.mkdir(exist_ok=True)
    windows = args.target == "windows-x86_64"
    command = ["zig", "c++", "-target", "x86_64-windows-gnu" if windows else "x86_64-linux-gnu.2.28",
               "-std=c++20", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wsign-conversion",
               "-Werror", "-Wno-nullability-completeness", "-isystem", str(include),
               "-isystem", str(root / "vendor" / "nvof"), str(root / "src" / "nvof_helper.cpp"),
               "-o", str(output / ("dlss-nvof-helper.exe" if windows else "dlss-nvof-helper"))]
    if not windows:
        command.append("-ldl")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
