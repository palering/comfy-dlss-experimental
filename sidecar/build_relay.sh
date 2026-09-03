#!/usr/bin/env bash
set -euo pipefail
relay_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${relay_dir}/build"
zig c++ -target x86_64-windows-gnu -std=c++20 -O2 -municode \
  -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  -Wno-nullability-completeness \
  "${relay_dir}/src/native_relay.cpp" -o "${relay_dir}/build/dlss-native-relay.exe" -lws2_32
zig c++ -target x86_64-windows-gnu -std=c++20 -O2 \
  -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  -Wno-nullability-completeness \
  "${relay_dir}/tests/mock_nr_worker.cpp" -o "${relay_dir}/build/mock-nr-worker.exe"
