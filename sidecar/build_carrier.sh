#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${script_dir}/build"
mkdir -p "${build_dir}"

zig c++ \
  -target x86_64-windows-gnu \
  -std=c++20 \
  -O2 \
  -fms-extensions \
  -Wall \
  -Wextra \
  -Wpedantic \
  -Wconversion \
  -Wsign-conversion \
  -Werror \
  -Wno-nullability-completeness \
  "${script_dir}/src/carrier.cpp" \
  "${script_dir}/src/frame_protocol.cpp" \
  "${script_dir}/src/frame_worker.cpp" \
  "${script_dir}/src/gpu_frame.cpp" \
  -o "${build_dir}/dlss-carrier.exe" \
  -lole32 \
  -luuid \
  -lws2_32 \
  -luser32

echo "${build_dir}/dlss-carrier.exe"
