# FFmpeg, ffprobe and PyAV

English · [简体中文](media-tools.zh-CN.md)

Audience: public

The node requires **host FFmpeg and ffprobe executables**, in addition to Python media packages. Installing ComfyUI or PyAV is not proof that these two commands are available.

## Which component does what?

| Component | Role |
| --- | --- |
| PyAV (`av`) | Python bindings to FFmpeg libraries; used by Comfy's native VIDEO and our decoding/materialization paths |
| `ffprobe` command | Input metadata inspection and output metadata/frame-count verification |
| `ffmpeg` command | Host video/audio export, encoding and color filter chain |
| Proton / NR Worker | Neural rendering; does not supply or launch the host media commands |

Comfy's [native video nodes](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_video.py) and [VIDEO implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_api/latest/_input_impl/video_types.py) use PyAV. [PyAV installation documentation](https://pyav.org/docs/stable/overview/installation.html) describes binary wheels linked against FFmpeg libraries. Those libraries are not a guarantee of a standalone ffmpeg/ffprobe command. Third-party Comfy distributions may include commands; inspect the actual backend instead of assuming.

## Optional Input Adapter paths

Two advanced string inputs are appended, preserving existing workflow positions:

- `ffmpeg_path`: absolute executable path **or directory containing the executables**. Blank uses Comfy backend PATH.
- `ffprobe_path`: optional independent executable/bin directory. If blank with explicit FFmpeg, use ffprobe in that selected directory. If both blank, resolve each on backend PATH.

Linux example: `/usr/bin` or `/opt/ffmpeg/bin/ffmpeg`.
Windows example: `D:\Tools\ffmpeg\bin` or `D:\Tools\ffmpeg\bin\ffmpeg.exe`.
These are paths on the **Comfy server**, not the browser computer, a Proton prefix or a remote URL. Tilde expands on the host. Spaces are supported; do not add quotes, shell commands or arguments.

An invalid explicit path or missing companion fails visibly; it never silently selects a different PATH version. Paths must resolve to executable files identifying themselves through `-version`. Version checks time out after five seconds and are cached by path/size/modification time. Only run binaries you trust.

## Inspect and execute

Click **Check input**. The in-node **Media tools (backend host)** section shows availability, resolved paths, selection source, version/error and the installed PyAV version. This is the **last check**, not live monitoring. A shell's login PATH may differ from a system service's PATH.

Preview and Process resolve tools again from the sequence configuration and record them in task details. Inspection, audio/video export and output verification use those selected tools. Selection is scoped to the task; it never edits global PATH or another workflow's configuration. Standalone diagnostic CLI scripts still use their own process PATH unless their interface provides an override.

Prepared-cache version 6 includes tool identity. Changing tools invalidates incompatible prepared inputs and derived original-preview exports; changing only Look retains matching caches. File identity is path/size/mtime plus reported version, **not a cryptographic binary integrity guarantee**.

## Install only when missing

Start with the [FFmpeg official download page](https://ffmpeg.org/download.html):
it links to platform packages/builds as well as source. Select a trusted host
distribution containing **both ffmpeg and ffprobe**, preserving any adjacent
libraries and license files. Reuse working tools instead of reinstalling them.
The node does not download/install FFmpeg, change system directories or modify
Comfy's PyTorch/CUDA environment. A separately selected bin directory works.

**ComfyUI has no required tools directory for this node.** You may keep a portable
distribution wherever convenient. The data-root tools layout in the installation
guide is only an optional example, not a Comfy standard or automatic search path.

<a id="pip-installation"></a>

### Optional pip installation

[static-ffmpeg](https://pypi.org/project/static-ffmpeg/) is a third-party option:
pip installs its manager; initialization downloads both platform executables.
This needs network access beyond PyPI and is not an official FFmpeg distribution.
For Linux uv, from Comfy root:

```sh
uv pip install --python .venv/bin/python static-ffmpeg
```

With Comfy's interpreter represented by `python` (for Windows portable, use
`python_embeded/python.exe` from the portable root), the pip equivalent and
explicit download/path lookup are:

```sh
python -m pip install static-ffmpeg
python -c "from static_ffmpeg import run; print(*run.get_or_fetch_platform_executables_else_raise(), sep='\n')"
```

Use **one** install command, not both. Paste the two returned paths into
`ffmpeg_path` and `ffprobe_path`. The node does not auto-discover this package or
initialize downloads; no global `add_paths()` call is needed. This distribution
has not completed our render/codec acceptance testing. `ffmpeg-python` alone
does not install the programs; `imageio-ffmpeg` alone does not supply the pair.

The current export path needs the codecs/filters it invokes, including libx264, AAC and scale/format/setparams. Passing `-version` proves executable availability, not every codec/filter or whole-file decodability. Actual exports verify dimensions, frame counts, timing, color and audio; unsupported builds fail explicitly.

Node backend/schema updates need a Comfy restart and browser refresh. A normal path-value change only requires a new input check and execution. Save unsaved workflows before refreshing.
