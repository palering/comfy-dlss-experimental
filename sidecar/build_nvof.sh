#!/usr/bin/env bash
set -euo pipefail
nvof_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# An existing CUDA header installation is enough; no nvcc / CUDA kernels.
nvof_include="${NVOF_CUDA_INCLUDE:-/opt/cuda/targets/x86_64-linux/include}"
test -f "${nvof_include}/cuda.h" || { echo 'Set NVOF_CUDA_INCLUDE to a directory containing cuda.h' >&2; exit 1; }
mkdir -p "${nvof_dir}/build"
zig c++ -target x86_64-linux-gnu -std=c++20 -O2 \
  -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  -Wno-nullability-completeness \
  -isystem "${nvof_include}" -isystem "${nvof_dir}/vendor/nvof" \
  "${nvof_dir}/src/nvof_helper.cpp" -o "${nvof_dir}/build/dlss-nvof-helper" -ldl
