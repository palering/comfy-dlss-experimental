#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${script_dir}/build"
ngx_include="${NGX_SDK_INCLUDE:-}"

if [[ -z "${ngx_include}" ]]; then
  echo "NGX_SDK_INCLUDE must point to the official NVIDIA DLSS SDK include directory" >&2
  exit 2
fi
for header in nvsdk_ngx.h nvsdk_ngx_defs.h nvsdk_ngx_params.h; do
  if [[ ! -f "${ngx_include}/${header}" ]]; then
    echo "required official SDK header not found: ${ngx_include}/${header}" >&2
    exit 3
  fi
done
mkdir -p "${build_dir}"
zig c++ \
  -target x86_64-windows-gnu \
  -std=c++20 \
  -O2 \
  -fms-extensions \
  -DCOMFY_DLSS_ENABLE_NGX=1 \
  -I "${ngx_include}" \
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
  "${script_dir}/src/ngx_smoke.cpp" \
  -o "${build_dir}/dlss-renderer.exe" \
  -lole32 \
  -luuid \
  -lws2_32 \
  -luser32

echo "${build_dir}/dlss-renderer.exe"
