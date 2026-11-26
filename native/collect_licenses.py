"""Collect notices for the exact downloaded/linked Cargo sources into package data."""
import argparse
import os
from pathlib import Path
import re
import shutil


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cargo-home',type=Path,default=Path(os.environ.get('CARGO_HOME',Path.home()/'.cargo')))
    args=p.parse_args()
    root=Path(__file__).resolve().parent.parent
    output=root/'pymessage'/'data'/'native-licenses'
    output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(root/'native'/'LICENSE',output/'imessage-database-GPL-3.0.txt')
    lock=(root/'native'/'Cargo.lock').read_text()
    index=['Native decoder linked dependencies. Upstream decoder: ReagentX/imessage-exporter tag 5.0.1.',
           'The companion is GPL-3.0-or-later. Cargo.lock records exact sources and checksums.\n']
    for package in lock.split('[[package]]')[1:]:
        name=re.search(r'^name = "([^"]+)"',package,re.M).group(1)
        version=re.search(r'^version = "([^"]+)"',package,re.M).group(1)
        matches=list((args.cargo_home/'registry'/'src').glob('*/'+name+'-'+version))
        if not matches:
            continue  # Packages for other target platforms are not linked here.
        source=matches[0]
        manifest=(source/'Cargo.toml').read_text()
        license_match=re.search(r'^license = "([^"]+)"',manifest,re.M)
        index.append(f'{name} {version}: {license_match.group(1) if license_match else "see bundled notices"}')
        for item in source.iterdir():
            if item.is_file() and item.name.upper().startswith(('LICENSE','COPYING','NOTICE','COPYRIGHT')):
                shutil.copyfile(item,output/(name+'-'+version+'-'+item.name))
    (output/'INDEX.txt').write_text('\n'.join(index)+'\n')


if __name__=='__main__':
    main()
