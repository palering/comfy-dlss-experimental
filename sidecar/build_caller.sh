#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ngx_include="${NGX_SDK_INCLUDE:-}"
for header in nvsdk_ngx.h nvsdk_ngx_defs.h nvsdk_ngx_params.h; do
  if [[ -z "$ngx_include" || ! -f "$ngx_include/$header" ]]; then
    echo "Set NGX_SDK_INCLUDE to the official NVIDIA DLSS SDK include directory (missing $header)." >&2
    exit 2
  fi
done
build_dir="$script_dir/build/caller"
mkdir -p "$build_dir"
export ZIG_GLOBAL_CACHE_DIR="${ZIG_GLOBAL_CACHE_DIR:-$script_dir/build/zig-cache-global}"
export ZIG_LOCAL_CACHE_DIR="${ZIG_LOCAL_CACHE_DIR:-$script_dir/build/zig-cache-local}"
zig c++ -target x86_64-windows-gnu -std=c++20 -O2 -shared \
  -DNGX_SNIPPET_BUILD -I "$ngx_include" -fno-optimize-sibling-calls -fno-lto \
  -fno-exceptions -fno-rtti -nostdlib++ \
  -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  -Wno-nullability-completeness \
  "$script_dir/src/nr_caller.cpp" -o "$build_dir/nvngx.dll"
echo "$build_dir/nvngx.dll"
