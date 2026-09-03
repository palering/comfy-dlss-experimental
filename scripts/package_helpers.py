"""Build ZIP release artifacts from our existing binaries; never includes vendor DLLs."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comfy_dlss_experimental.helper_artifacts import REPO, TARGETS, helper_names, sha256, validate_version


def package(target, version, build_dir, output):
    validate_version(version)
    names = helper_names(target)
    files = {name: build_dir / name for name in names.values()}
    # Require real target formats; don't label a Linux binary as a Windows EXE.
    for role, name in names.items():
        with files[name].open("rb") as stream:
            signature = stream.read(4)
        expected = b"\x7fELF" if target == "linux-x86_64" and role == "nvof" else b"MZ"
        if not signature.startswith(expected):
            raise ValueError(f"Wrong binary format: {files[name]}")
    notices = "NVIDIA Optical Flow API header notices (not a bundled driver/runtime):\n\n"
    for name in ("nvOpticalFlowCommon.h", "nvOpticalFlowCuda.h"):
        text = (REPO / "sidecar" / "vendor" / "nvof" / name).read_text(encoding="utf-8")
        notices += name + "\n" + text[:text.index("*/") + 2] + "\n\n"
    import hashlib
    manifest = {"schema_version": 1, "version": version, "target": target,
                "files": {name: sha256(path) for name, path in files.items()}}
    manifest["files"]["THIRD_PARTY_NOTICES.txt"] = hashlib.sha256(notices.encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"comfy-dlss-helpers-{version}-{target}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files.items():
            bundle.write(path, name)
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        bundle.writestr("THIRD_PARTY_NOTICES.txt", notices)
    checksum = f"{sha256(archive)}  {archive.name}\n"
    archive.with_suffix(".zip.sha256").write_text(checksum, encoding="utf-8")
    print(checksum, end="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-dir", type=Path, default=REPO / "sidecar" / "build")
    parser.add_argument("--output", type=Path, default=REPO / "dist")
    args = parser.parse_args()
    package(args.target, args.version, args.build_dir, args.output)
