# Preparing external runtime components

Audience: public

The active backend is **direct NR**. It does not require ReShade, RenoDX,
Streamline or the converter's web server.

## Required files

| Component | Role | Provided by |
| --- | --- | --- |
| `dlss-native-relay.exe` | Transport/process bridge | This project's helper package or source build |
| `nvngx.dll` | Standalone Windows video Worker executable implementing D5V2 | User |
| `nvngx_dlssnr.dll` | Matching NR model/runtime loaded by that Worker | User |

The Worker named `nvngx.dll` is **not** the driver NGX DLL or a generic
`caller/nvngx.dll` shim. An arbitrary same-name file will not work.
Use a trusted Worker/model pair that was supplied together. Do not assume
different GPU-series packages, fixes or protocol revisions are interchangeable.

We do not provide or automatically download these external binaries.
Consult the original distributor's terms and compatibility instructions.
A newer driver is advisable but does not make every experimental model work.
Linux/Proton validation covers one RTX 4070 Ti SUPER combination, not all
RTX 40/50 cards; Windows GPU validation is still pending.

## Versioned local storage

Keep binaries outside the Git repository, for example:

```text
ComfyUI/user/default/comfy-dlss-experimental/
  components/
    nr/<bundle-version>/nvngx.dll
    nr/<bundle-version>/nvngx_dlssnr.dll
  runtime-presets/default.json
```

Copy [direct-nr.example.json](../examples/runtime-presets/direct-nr.example.json)
into `runtime-presets/default.json`. Point its Worker/model entries to your
local files. Relative paths resolve from the preset JSON's directory.
`relay: "@bundled/relay"` selects the installed project helper.

Use a separate directory for each bundle version rather than replacing files
in place. Record the source and exact hashes. Jobs use hashed runtime snapshots;
changing component bytes requires a different instance. Never load untrusted
binaries: process separation is not a security sandbox.

## Optional components and diagnostics

NVIDIA optical flow uses a separate **native** helper and the host driver
libraries. It does not require another NR DLL. DIS remains an independent
CPU optical-flow choice. See [NVIDIA flow setup](nvidia-optical-flow.md).

The old ReShade carrier diagnostic can bind ReShade's add-on-enabled x64
`dxgi.dll`, RenoDX `.addon64`, compatible DLSS/DLSSNR files and, where needed,
native `D3DCompiler_47.dll`. These are **not requirements for direct NR**.

For that proxy-DLL diagnostic only, a runtime preset can use
`d3dcompiler_47=n;dxgi=n,b`. Wine's `n` means native and `b` means builtin;
the setting neither downloads libraries nor fixes a missing video protocol.
Overrides apply to the launched child, not global Wine configuration.

A [component manifest example](../examples/component-manifests/renodx-dlss5.example.json)
is provided for the optional diagnostic registry; it contains no runtime files.
