# Installation, platforms and helper distribution

English · [简体中文](distribution.zh-CN.md)

Audience: public

Status: 2026-09-03. The [public repository](https://github.com/palering/comfy-dlss-experimental) ships source, not build artifacts. The Release ZIP installer is implemented, but **prebuilt assets have not been published**. Build locally or wait for platform-validated packages. No automatic downloads of external NR Workers/models, Proton, drivers or CUDA SDK.

## User installation: clone, dependencies, then validation

### Step 1: clone only the source into custom_nodes

From the ComfyUI root:

```sh
git clone https://github.com/palering/comfy-dlss-experimental.git custom_nodes/comfy-dlss-experimental
```

Do not move Comfy to a new environment or unpack the whole converter over the node.
Source, Python packages, external runtimes and our build artifacts are separate.

### Step 2: identify Comfy's Python

Linux with uv/.venv, from ComfyUI root:

```sh
.venv/bin/python -c "import sys; print(sys.executable); print(sys.prefix)"
.venv/bin/python -c "import numpy, cv2, av, PIL; assert hasattr(cv2, 'DISOpticalFlow_create'); print(cv2.__file__); print(av.__file__)"
```

Install only packages confirmed missing. For example, if no cv2 provider exists:

```sh
uv pip install --python .venv/bin/python opencv-python-headless
```

Missing numpy, av or PIL correspond to numpy, av and Pillow. Preserve environment
constraints and review resolution changes; do not reinstall everything or add
--upgrade. If pip is available, `.venv/bin/python -m pip install <missing-package>`
also targets the correct interpreter.

Windows portable, from the **portable distribution root**:

```powershell
.\python_embeded\python.exe -c "import sys; print(sys.executable)"
.\python_embeded\python.exe -c "import numpy, cv2, av, PIL; assert hasattr(cv2, 'DISOpticalFlow_create'); print(cv2.__file__)"
# Only if no installed package provides cv2:
.\python_embeded\python.exe -m pip install opencv-python-headless
```

Ordinary Windows venv installations use Comfy's .venv\Scripts\python.exe.
Conda/other managers must likewise select the interpreter **launching Comfy**,
not the browser machine or default system Python. Keep the distribution's
python_embeded spelling; do not create another environment to compensate.

OpenCV imports as cv2 and installs into the selected interpreter's site-packages
(Lib/site-packages on some Windows layouts), not the DLL folder. Reuse an existing
opencv-python/contrib/headless provider that imports and exposes DIS. DIS does
not require automatically replacing it with contrib or a CUDA OpenCV build.
The [OpenCV packaging guide](https://pypi.org/project/opencv-python-headless/)
warns that variants share cv2: **do not install several variants together**.
If an installed provider fails or lacks functionality, diagnose first rather
than layering headless over it. Unpacking wheels into node-local vendor and
altering sys.path risks conflicts with Comfy and other nodes.

requirements.txt is currently informational: `pip install -r requirements.txt`
**does not install the media packages**. The installer does not automatically
modify PyTorch, CUDA, NumPy or remove existing OpenCV.

### Step 3: reuse or select host FFmpeg

Reuse existing working ffmpeg/ffprobe commands. Linux distro packages commonly
live in /usr/bin; Windows can use an existing installation or portable build.
No system-directory modification is required for this node.

For a private portable installation, follow the [FFmpeg download entry](https://ffmpeg.org/download.html)
for your host and extract the **complete distribution** including required shared
libraries and notices into the tools layout below. Do not extract only two EXEs
from a build requiring adjacent DLLs. The official page distinguishes source from
external prebuilt distributors; we do not present third-party builds as our own
official FFmpeg binaries.

The directory is your choice: **ComfyUI does not require a tools directory**.
The layout below is an optional example, not an installation prerequisite.
Alternatively, see [pip-based setup](media-tools.en.md#pip-installation) for
static-ffmpeg, its explicit first download and manual node-path configuration.

Set Input Adapter's advanced ffmpeg_path to the absolute bin directory; companion
ffprobe may stay blank. The tools directory is a convention, **not automatically
searched yet**. If backend PATH already works, leave both blank.
On Linux do not use the converter's Windows ffmpeg.exe or run media commands
through Proton. Python ffmpeg/ffmpeg-python packages do not replace executable
commands. See [media tools](media-tools.en.md).

### Step 4: install our helpers

The intended user experience is **prebuilt packages, no Zig/CUDA development setup**.
[Our Releases](https://github.com/palering/comfy-dlss-experimental/releases) had no
assets when checked on 2026-09-03. Currently a maintainer must supply a trusted
locally built package, or users must wait for the first helper Release. This is
not an already-complete one-click download experience.

Package names:

- `comfy-dlss-helpers-<version>-linux-x86_64.zip`
- `comfy-dlss-helpers-<version>-windows-x86_64.zip`

From the node root, using Comfy's interpreter (represented here by python):

```sh
python install.py --archive /path/to/helpers.zip --sha256 <trusted-64-character-SHA256>
```

The existing explicit HTTPS option also accepts a known exact Release asset:

```sh
python install.py --url https://<release-host>/<versioned-package>.zip --sha256 <trusted-hash>
```

The installer selects the actual host, not browser OS. Files go into
`sidecar/bin/<target>/<version>/`, with active.json in the target directory.
Each package contains relay, platform NVOF helper, manifest.json and
THIRD_PARTY_NOTICES.txt. DIS does not execute NVOF, although standard packages
currently contain both helpers.

The installer validates ZIP SHA-256, internal hashes, target label, sizes and
allowlists, rejecting traversal, symlinks, duplicates and external DLLs. Binary
format checks happen during packaging; labels/hashes are not GPU acceptance.
Conflicting same-version files are not overwritten; old versions remain. Hashes
do not make an untrusted publisher safe. Stop DLSS work before updating and
restart Comfy afterward. Invalid packages never silently fall back to developer
builds. A node manager invoking install.py without arguments only prints guidance,
without network access or dependency installation.

### Step 5: place the external Worker/model and create a preset

Use the [exact provenance and archive paths](DLL_PREPARATION.en.md), placing both
files into a versioned components/nr folder. Copy
examples/runtime-presets/direct-nr.example.json to the **data root's**
runtime-presets/default.json. The example now matches the layout below; adjust
paths for other versions and keep relay as @bundled/relay.

**Do not pass the converter ZIP to install.py**: it accepts only our helper format
and rejects external DLLs. Runtime acquisition/import is currently manual, not
automatic searching of websites, Discord or driver directories.

### Step 6: inspect before rendering

Restart Comfy, save unsaved workflows before refreshing, then import an
[example](../example_workflows/README.en.md). Inspect media and actual executable
paths in Input Adapter. Select host/preset in Runtime; Linux also selects installed
Proton and needs Steam/Xwayland. Test one frame, then a short range, then full output.
Metadata/hashes are preflight, not proof of successful Create/Evaluate or quality.

## Recommended layout

```text
ComfyUI/
  .venv/                                      # Python packages for a venv install
  custom_nodes/comfy-dlss-experimental/        # Git source
    install.py
    sidecar/
      build/                                  # Maintainer outputs, Git-ignored
      bin/<target>/
        active.json
        <helper-version>/
          dlss-native-relay.exe
          dlss-nvof-helper[.exe]
          manifest.json
          THIRD_PARTY_NOTICES.txt
  user/default/comfy-dlss-experimental/       # Default node data root
    components/nr/converter-v0.1.0-rtx40/
      nvngx.dll
      nvngx_dlssnr.dll
    runtime-presets/default.json
    tools/ffmpeg/<host-target>/<version>/
      bin/ffmpeg[.exe]
      bin/ffprobe[.exe]
      ...                                     # Remaining distribution/notices
    runtime-snapshots/                         # Generated; do not edit
    prepared-clips/
    prefixes/                                 # Linux/Proton
    executions/
```

Portable Windows python_embeded is a **sibling of ComfyUI** under the portable
root, not this .venv. COMFY_DLSS_HOME or Comfy --user-directory changes the data
root. components/tools are placement conventions, not new automatic discovery;
custom paths still work. Existing software, runtimes and caches are not moved.

## Simpler setup: current behavior versus next steps

| Operation | Current state | Proposed simplification (not implemented) |
| --- | --- | --- |
| Helper ZIP/HTTPS + hash | install.py exists; no public assets yet | Publish reviewed platform packages so users do not compile |
| Python checks | Interpreter commands/node imports | Read-only doctor reporting paths/versions/DIS/conflicts |
| Python installation | Explicitly install missing packages into Comfy | Opt-in missing-only setup, resolution preview, no silent OpenCV replacement/NumPy upgrade |
| FFmpeg selection | PATH or validated/reported Input Adapter paths | Versioned portable-package installation after source/hash review, retaining complete distribution |
| Worker/model | Manual files/preset with execution fingerprints/snapshots | Local-only import, file checks, versioned copies and generated preset; no online DLL search |
| Driver/Proton | Existing user installations | Doctor guidance, no automated driver install/system edits/game-prefix changes |

A unified setup/doctor entry should first **inspect and show a plan**, then perform
only explicitly approved actions. Do not put everything in one vendor folder or
install packages at node import. Proposed rows are not existing CLI arguments.
This turn documents the route and adjusts the example, not a complete installer.

## Component boundaries

| Component | Linux | Windows | Source |
| --- | --- | --- | --- |
| Node Python/frontend | Host | Host | Repository |
| dlss-native-relay.exe | Proton | Native | Same project PE |
| dlss-nvof-helper / .exe | Native ELF | Native PE | Separate project builds |
| nvngx.dll Worker | Relay launches | Relay launches | User |
| nvngx_dlssnr.dll | Worker loads | Worker loads | User |

Here nvngx.dll is an external Windows executable, not the driver NGX library. Arbitrary shims/models need not satisfy D5V2. Runtime files, caches and logs stay in the selected user data root, not system directories. Packages, builds and CUDA build headers are Git-ignored. Development builds retain sidecar/build discovery; an invalid installed package never silently falls back.

## Runtime settings

- Platform auto/windows/linux uses the **backend host**, not the browser OS. Manual choices help edit workflows but execution must match the real host; this is not a remote-machine selector.
- Windows runs natively, hides/ignores Proton and Linux display fields, and skips Proton discovery, Xwayland scanning, fcntl and /proc flow probes.
- Linux execution is proton or native_reserved; the latter explicitly rejects as unimplemented. It is a different extension axis from the nvidia_official model backend.
- Custom Proton path takes precedence over discovery. It accepts an absolute installation directory/proton file and ~, no extra commands or shell. Invalid paths fail without version fallback.
- Linux NR needs Steam's client directory and valid Xwayland DISPLAY. Prefer a recent stable driver. RTX 4070 Ti SUPER / 610.57.04 / GE-Proton 11-6 is a tested combination, not a minimum requirement.

Old widget positions stay unchanged; appended fields default safely. Hidden fields remain serialized for cross-platform editing. Execution does not trust stale catalog host data. Windows uses TEMP/TMP; Linux uses TMPDIR/XDG_CACHE_HOME. This is backend separation, not just UI hiding.

## Optical flow and validation limits

Both helpers use the CUDA Optical Flow interface and common motion protocol. Linux loads libcuda.so.1/libnvidia-opticalflow.so.1; Windows loads nvcuda.dll/nvofapi64.dll only from the system directory and uses binary streams with bounded threaded pipes, not POSIX selectors on anonymous pipes. Driver libraries stay out of Comfy Python. See [NVIDIA's interface guide](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html).

Windows cross-compilation and generic backpressure/log/binary/cancellation/timeout tests are **not Windows GPU acceptance**. NR, NVOF, resident release and Chinese paths still need real Windows tests. Owned-process resource collection remains Linux-only; device-wide figures are not presented as Windows worker usage.

## Maintainer builds

These steps also serve users building from source before prebuilt Releases are
available. They build **our relay and optional NVOF helper**, never the external
video Worker or proprietary NR model. Work from the node repository root.

### 1. Prepare the build tools

- Python 3.11+ for the NVOF build/packaging scripts; use Comfy's interpreter for
  installation and media tests. No additional Python build package is required.
- Zig on PATH with C++20/cross-target support; scripts invoke `zig c++`.
- Bash for build_relay.sh. On Windows, use an existing Bash environment such as
  Git Bash with Zig on its PATH, or cross-build elsewhere and copy the artifacts.
  This shell is a build tool, not part of the Windows NR runtime requirements.
- **NVIDIA flow only:** existing NVIDIA CUDA API headers, including cuda.h and
  its includes. Optical Flow API headers are already under sidecar/vendor/nvof.
  No nvcc, CUDA kernel compilation, MSVC host or NGX SDK is needed here.

Build scripts do not fetch/install compilers or SDKs. Building does not require
an NVIDIA GPU; running NR/NVOF still does. macOS can be a cross-build host, not an
NR execution target. Windows GPU validation remains pending.

### 2. Build only the relay for DIS

```sh
bash sidecar/build_relay.sh
```

Outputs in sidecar/build:

- `dlss-native-relay.exe`: Windows x64 PE for native Windows or Linux/Proton.
- `mock-nr-worker.exe`: GPU-free transport-test fixture, not NR and not distributed.

No CUDA headers are needed for this step. Keep the preset's `@bundled/relay`:
on a fresh installation with no `sidecar/bin/<target>/active.json` it resolves
the build output directly. This is enough to try DIS with your external pair.
An existing installed manifest takes precedence; rebuilding sidecar/build does
not replace it. Use a new versioned package to update an installed helper.

### 3. Optionally build NVIDIA flow

Select the **Comfy host target**, independently of the build/browser host:

```sh
python sidecar/build_nvof.py --target linux-x86_64 --cuda-include /path/to/cuda/include
python sidecar/build_nvof.py --target windows-x86_64 --cuda-include /path/to/cuda/include
```

Run only your desired target, or both for distribution. Outputs are
sidecar/build/dlss-nvof-helper (ELF, Linux glibc 2.28 target) and
sidecar/build/dlss-nvof-helper.exe (Windows PE). The helper loads the host driver
interfaces dynamically; CUDA/Optical Flow driver libraries are not packaged.

### 4. Package and install a local build

The standard package requires **both relay and NVOF for that target**, even if
the eventual workflow uses DIS. For a relay-only test use the build fallback
above; do not create a partial ZIP. For example, after building both Linux-package helpers:

```sh
python scripts/package_helpers.py --target linux-x86_64 --version local-1
```

This creates dist/comfy-dlss-helpers-local-1-linux-x86_64.zip and its .zip.sha256.
For Windows choose windows-x86_64 and its corresponding output filename.
local-1 is an example build label, not a published release. Choose a new label
for changed builds: packaging does not overwrite existing archives.

Read the checksum file. On the **target Linux/Windows host**, with Comfy's Python,
replace the hash placeholder with its 64-character value and install:

```sh
python install.py --archive dist/comfy-dlss-helpers-local-1-linux-x86_64.zip --sha256 <SHA256_FROM_CHECKSUM_FILE>
```

Use the Windows archive name on Windows. Stop DLSS tasks before updating;
installation writes `sidecar/bin/<target>/<version>/` and active.json. Restart
Comfy afterwards. For a cross-build, transfer the ZIP/checksum to the target
first; do not run the installer on macOS expecting to install a Linux target.

Packages contain only our two helpers, a version/hash manifest and required
NVOF header notices. Mock Worker, external DLLs and CUDA headers are excluded;
build/, bin/ and dist/ outputs are Git-ignored. Packaging does not publish to
GitHub. Source is MIT; see [third-party notices](../THIRD_PARTY_NOTICES.md).

Before sharing artifacts, review embedded build paths, run the
[development checks](DEVELOPMENT.en.md#portable-validation), and complete target
GPU acceptance. Windows/Linux CI tests and a successful cross-build do not
prove Windows NR/NVOF behavior. Only explicitly opted-in tests use a GPU.

## Verified scope

Linux hardware checks cover one-frame NR, NVOF, installed helpers, custom Proton paths and host mismatch rejection. Python/frontend tests cover protocols, installation, parameters and conditional fields, not all UI/long-video/GPU combinations. Windows NVIDIA acceptance remains incomplete.
