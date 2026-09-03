# Third-party boundaries

Audience: public

Original project code is provided under [MIT](LICENSE). That license does not
relicense third-party components or grant rights to external runtime binaries.

## Included source declarations

`sidecar/vendor/nvof/nvOpticalFlowCommon.h` and `nvOpticalFlowCuda.h` are NVIDIA
Optical Flow API headers from commit `edb50da3cf849840d680249aa6dbef248ebce2ca`.
Their NVIDIA copyright and three-clause redistribution notices remain intact
in the headers. See [provenance](sidecar/vendor/nvof/README.md). Helper ZIPs must
include those notices, as the packaging script does.

## External components not included

- The selected standalone NR Worker and `nvngx_dlssnr.dll` model/runtime.
- Other NVIDIA DLSS/NGX binaries, CUDA headers/toolkit, and graphics drivers.
- ReShade, RenoDX, Proton, FFmpeg binaries and third-party Python packages.

Users obtain and use these separately under their applicable terms. Different
NR Worker builds may implement incompatible protocols. This repository does
not automatically fetch them, and does not bundle the reference projects used
for investigation. Source links are references, not endorsements or permission
to redistribute their binaries.

The [external-file guide](docs/DLL_PREPARATION.en.md) identifies the exact tested
converter archive and distinguishes its executable Worker from the driver NGX
library and caller shim. Upstream source attribution does not establish a
reproducible build or grant rights to redistribute its modified model.

This is an independent experimental project, not an NVIDIA product. NVIDIA,
DLSS and other product names belong to their respective owners.
