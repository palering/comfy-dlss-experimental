"""Build the explicit CXR1 Streamline reconstruction Worker; not the default NR/CSR1 binary."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--streamline-include', required=True)
    parser.add_argument('--zig', default='zig')
    parser.add_argument('--native-tests', action='store_true')
    args = parser.parse_args()
    sdk = Path(args.streamline_include).resolve()
    headers = ('sl.h', 'sl_dlss.h', 'sl_dlss_d.h', 'sl_core_api.h', 'sl_version.h')
    for name in headers:
        if not (sdk / name).is_file(): parser.error('Missing official Streamline header: ' + name)
    root = Path(__file__).resolve().parent
    build = root / 'build/sl-worker'
    build.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(root / 'build/zig-cache-global'),
               ZIG_LOCAL_CACHE_DIR=str(root / 'build/zig-cache-local'))
    common = ['-std=c++20', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Wconversion', '-Wsign-conversion',
              '-Werror', '-I', str(root / 'src')]
    if args.native_tests:
        for name in ('sl_sr_input_contract', 'sl_rr_input_contract', 'sl_wire'):
            output = build / (name + '_test')
            subprocess.run([*shlex.split(os.environ.get('CXX', 'clang++')), *common, '-O1', '-g',
                '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                str(root / 'tests' / (name + '_test.cpp')), '-o', str(output)], check=True, env=env)
            subprocess.run([str(output)], check=True, env=env)
    output = build / 'comfy-dlss-sl-worker.exe'
    command = [args.zig, 'c++', '-target', 'x86_64-windows-gnu', *common, '-municode', '-isystem', str(sdk),
        str(root / 'src/sl_worker_main.cpp'), str(root / 'src/sl_sr_engine.cpp'), '-o', str(output),
        '-luuid', '-lws2_32', '-luser32']
    subprocess.run(command, check=True, env=env, cwd=root.parent)
    report = dict(kind='streamline_reconstruction_worker', protocol='CXR1', gpu_validated=False,
                  target='x86_64-windows-gnu', command=command,
                  sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  sdk_header_hashes={name: hashlib.sha256((sdk / name).read_bytes()).hexdigest() for name in headers})
    (build / 'build.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
