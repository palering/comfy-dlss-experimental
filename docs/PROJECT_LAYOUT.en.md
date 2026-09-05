# Project and runtime directory layout

English · [简体中文](PROJECT_LAYOUT.zh-CN.md)

Audience: public

The Git checkout contains source, tests and templates. User-supplied runtime
files and generated data deliberately live outside the checkout so `git pull`,
node removal and version switching do not overwrite them.

## Repository installed under ComfyUI

```text
ComfyUI/
└── custom_nodes/
    └── comfy-dlss-experimental/
        ├── __init__.py                 # Comfy entrypoint; exports WEB_DIRECTORY
        ├── comfy_dlss_experimental/
        │   ├── nodes/                  # Comfy V3 node schemas and execution entrypoints
        │   ├── setup_check.py          # read-only Setup Helper diagnostics
        │   ├── video_pipeline.py       # preparation, Worker orchestration and export
        │   ├── direct_nr.py            # D5V2 Worker protocol client
        │   ├── presets.py              # preset validation and component hashes
        │   ├── platform_runtime.py      # Windows-native / Linux-Proton selection
        │   └── ...                     # input, flow, preview and lifecycle modules
        ├── web/                         # in-node cards and frontend localization
        ├── locales/{en,zh,zh-TW}/       # Comfy node labels and tooltips
        ├── example_workflows/           # importable current workflow JSON files
        ├── examples/runtime-presets/    # templates only; no proprietary DLLs
        ├── sidecar/
        │   ├── src/                     # source for our relay/NVOF helpers
        │   ├── vendor/nvof/             # NVIDIA header provenance and retained notices
        │   ├── build/                    # ignored local build output
        │   └── bin/<target>/<version>/  # installed helper Release, if available
        ├── scripts/                      # opt-in diagnostics/build validation
        ├── tests/                        # non-GPU unit and integration scaffolding
        ├── docs/                         # paired English/Chinese public documentation
        └── install.py                    # installs only our explicitly checksummed helper ZIP
```

`sidecar/build/`, `sidecar/bin/`, external DLLs, videos, caches and private agent
notes are ignored. A clone therefore does not contain an executable NR runtime.

## User data root

By default this is `ComfyUI/user/default/comfy-dlss-experimental/`. A custom
Comfy user directory changes the prefix; `COMFY_DLSS_HOME` can override the
entire node data root.

```text
comfy-dlss-experimental/
├── components/
│   └── nr/
│       └── converter-v0.1.0-rtx40/     # user-managed, versioned external pair
│           ├── nvngx.dll               # external video Worker executable
│           └── nvngx_dlssnr.dll        # matching NR model/runtime
├── runtime-presets/
│   ├── default.json                    # selected bindings; user-managed
│   └── another-version.json            # optional alternate validated pair
├── prepared-clips/                     # generated color/motion caches
├── runtime-snapshots/                  # generated immutable hash-keyed copies
├── prefixes/                           # generated Proton compatibility data
├── executions/                         # generated copyable execution records
├── cache/                              # generated Worker/runtime cache
├── tmp/                                # generated Worker temporary files
└── jobs/                               # retained legacy carrier diagnostics only
```

Only `components/` and `runtime-presets/` are manually prepared. The application
creates the other directories when required. Do not edit `runtime-snapshots/`;
change the source component directory and select/create a new preset instead.

## Process boundary

```text
Comfy nodes (Python)
  └─ dlss-native-relay.exe               # ours; PE transport/process supervisor
       └─ nvngx.dll --video              # external Worker; Windows PE executable
            └─ nvngx_dlssnr.dll          # matching external model/runtime
```

On Windows both PE programs run natively. On Linux the relay and Worker run
through the selected Proton installation and Xwayland; FFmpeg and the optional
NVIDIA optical-flow helper remain host-native. No systemd service is required.

See [external runtime files](DLL_PREPARATION.en.md),
[installation/distribution](distribution.en.md) and [architecture](ARCHITECTURE.en.md).
