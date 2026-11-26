"""Build, checksum and zip a self-contained macOS application for this Python's architecture."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ARCH = platform.machine()
OUT = ROOT / 'dist' / 'macos' / ARCH
WORK = ROOT / 'build' / 'macos' / ARCH


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if platform.system() != 'Darwin' or ARCH not in ('x86_64', 'arm64'):
        raise SystemExit('Run on a Mac with Apple Silicon or Intel Python.')
    name = 'pymessage-decoder-' + ARCH
    source = ROOT / 'pymessage' / 'bin'
    if sha(source / name) != json.loads((source / 'manifest.json').read_text())[ARCH]['sha256']:
        raise SystemExit('Source decoder failed its integrity check.')
    WORK.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(WORK / 'cache'), MPLCONFIGDIR=str(WORK / 'mpl'))
    subprocess.run(['/usr/bin/arch', '-' + ARCH, sys.executable, '-m', 'PyInstaller',
                    '--noconfirm', '--distpath', str(OUT), '--workpath', str(WORK),
                    str(ROOT / 'packaging/macos/Py-Message.spec')], cwd=ROOT, env=env, check=True)
    app = OUT / 'Py-Message.app'
    # PyInstaller relocates and ad-hoc signs Mach-O files. Verify those final bytes at runtime.
    data = app / 'Contents/Resources/pymessage/bin'
    (data / 'manifest.json').write_text(json.dumps({ARCH: {'file': name, 'sha256': sha(data / name)}}, indent=2) + '\n')
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    target = OUT / ('Py-Message-' + ('Apple-Silicon' if ARCH == 'arm64' else 'Intel') + '.zip')
    subprocess.run(['ditto', '-c', '-k', '--keepParent', '--norsrc', str(app), str(target)], check=True)
    print(target)


if __name__ == '__main__':
    main()
