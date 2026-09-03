#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${script_dir}/build"
mkdir -p "${build_dir}"
extra_flags=()
if [[ "${SANITIZE:-0}" == "1" ]]; then
  extra_flags=(-fsanitize=address,undefined -fno-omit-frame-pointer -g)
fi

"${CXX:-c++}" \
  -std=c++20 \
  -O2 \
  -Wall \
  -Wextra \
  -Wpedantic \
  -Wconversion \
  -Wsign-conversion \
  -Werror \
  "${extra_flags[@]}" \
  -I "${script_dir}/src" \
  "${script_dir}/src/frame_protocol.cpp" \
  "${script_dir}/tests/frame_protocol_test.cpp" \
  -o "${build_dir}/frame-protocol-test"

"${build_dir}/frame-protocol-test"
