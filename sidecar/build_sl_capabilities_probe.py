"""Cross-build public Streamline feature queries; no inference or vendor copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--streamline-include', required=True)
    parser.add_argument('--zig', default='zig')
    args = parser.parse_args()
    sdk = Path(args.streamline_include).resolve(strict=True)
    for name in ('sl.h', 'sl_dlss.h', 'sl_dlss_g.h', 'sl_dlss_d.h'):
        if not (sdk / name).is_file():
            parser.error('Missing public header: ' + name)
    root = Path(__file__).resolve().parent
    build = root / 'build/sl-capabilities'; build.mkdir(parents=True, exist_ok=True)
    target = build / 'comfy-sl-capabilities.exe'
    command = [args.zig, 'c++', '-target', 'x86_64-windows-gnu', '-std=c++20', '-O2',
        '-Wall', '-Wextra', '-Wpedantic', '-Wconversion', '-Wsign-conversion', '-Werror',
        '-I', str(root / 'src'), '-isystem', str(sdk), '-municode',
        str(root / 'src/sl_capabilities_probe.cpp'), '-o', str(target), '-luuid']
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(root / 'build/zig-cache-global'),
               ZIG_LOCAL_CACHE_DIR=str(root / 'build/zig-cache-local'))
    subprocess.run(command, env=env, check=True)
    report = {'command': command, 'inference_tested': False, 'runtime_queried': False,
              'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (build / 'build.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
