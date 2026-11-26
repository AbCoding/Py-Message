"""Build both Mac binaries and refresh their integrity manifest (developer tool)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cargo',default='cargo')
    p.add_argument('--target',action='append',choices=['aarch64-apple-darwin','x86_64-apple-darwin'])
    args=p.parse_args()
    native=Path(__file__).resolve().parent
    output=native.parent/'pymessage'/'bin'
    output.mkdir(parents=True,exist_ok=True)
    for target in args.target or ['aarch64-apple-darwin','x86_64-apple-darwin']:
        subprocess.run([args.cargo,'build','--release','--locked','--target',target],cwd=native,check=True)
        arch='arm64' if target.startswith('aarch64') else 'x86_64'
        destination=output/('pymessage-decoder-'+arch)
        shutil.copyfile(native/'target'/target/'release'/'pymessage-decoder',destination)
        subprocess.run(['/usr/bin/strip','-x',str(destination)],check=True)
        destination.chmod(0o755)
    manifest={arch:{'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
              for arch in ('arm64','x86_64') if (path:=output/('pymessage-decoder-'+arch)).exists()}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':
    main()
