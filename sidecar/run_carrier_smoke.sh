#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 7 ]]; then
  echo "usage: $0 PROTON PREFIX JOB_DIR RESHADE_DLL RENODX_ADDON DLSS_DLL DLSSNR_DLL" >&2
  exit 2
fi

proton="$1"
prefix="$2"
job_dir="$3"
reshade_dll="$4"
renodx_addon="$5"
dlss_dll="$6"
dlssnr_dll="$7"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
carrier="${script_dir}/build/dlss-carrier.exe"

for required_file in \
  "${proton}" \
  "${carrier}" \
  "${reshade_dll}" \
  "${renodx_addon}" \
  "${dlss_dll}" \
  "${dlssnr_dll}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "required file not found: ${required_file}" >&2
    exit 3
  fi
done

if [[ -e "${job_dir}" ]]; then
  echo "refusing to reuse smoke-test job directory: ${job_dir}" >&2
  exit 4
fi

mkdir -p "${prefix}"
mkdir -p "${job_dir}"
cp -- "${carrier}" "${job_dir}/dlss-carrier.exe"
cp -- "${reshade_dll}" "${job_dir}/dxgi.dll"
cp -- "${renodx_addon}" "${job_dir}/renodx-dlss5.addon64"
cp -- "${dlss_dll}" "${job_dir}/nvngx_dlss.dll"
cp -- "${dlssnr_dll}" "${job_dir}/nvngx_dlssnr.dll"

steam_root="${HOME}/.local/share/Steam"
set +e
env \
  WINEDLLOVERRIDES="d3dcompiler_47=n;dxgi=n,b" \
  STEAM_COMPAT_DATA_PATH="${prefix}" \
  STEAM_COMPAT_CLIENT_INSTALL_PATH="${steam_root}" \
  timeout 90s \
  "${proton}" run \
  "${job_dir}/dlss-carrier.exe" \
  --result "${job_dir}/carrier-result.json" \
  --presents 180 \
  --interval-ms 8
carrier_status=$?
set -e

if [[ -f "${job_dir}/carrier-result.json" ]]; then
  cat "${job_dir}/carrier-result.json"
else
  echo "carrier result was not created" >&2
fi
echo "carrier process exit status: ${carrier_status}" >&2
exit "${carrier_status}"
