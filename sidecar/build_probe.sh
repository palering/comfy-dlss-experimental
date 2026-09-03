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
  -Wno-nullability-completeness \
  "${script_dir}/src/probe.cpp" \
  -o "${build_dir}/dlss-sidecar-probe.exe" \
  -lole32 \
  -luuid

echo "${build_dir}/dlss-sidecar-probe.exe"
