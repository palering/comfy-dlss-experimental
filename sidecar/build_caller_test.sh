#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${NGX_SDK_INCLUDE:-}" || ! -f "$NGX_SDK_INCLUDE/nvsdk_ngx.h" ]]; then
  echo "Set NGX_SDK_INCLUDE to the official NVIDIA DLSS SDK include directory." >&2
  exit 2
fi
build_dir="$script_dir/build/caller-tests"
mkdir -p "$build_dir"
"${CXX:-clang++}" -std=c++20 -O1 -g -DNGX_SNIPPET_BUILD \
  -I "$NGX_SDK_INCLUDE" -I "$script_dir/src" -fno-optimize-sibling-calls -fno-lto \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  "$script_dir/src/nr_caller.cpp" "$script_dir/tests/nr_caller_test.cpp" \
  -o "$build_dir/nr-caller-test"
"$build_dir/nr-caller-test"
echo "Caller argument/result/null-target tests passed (ASan + UBSan; no NGX/GPU loaded)."
