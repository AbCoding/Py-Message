# Build with the target architecture's Python and wheels. See README.md.
from importlib.metadata import distributions
from pathlib import Path
import platform
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parents[1]
sys.path.insert(0, str(root))
from pymessage import __version__

arch = platform.machine()
package = root / 'pymessage'
datas = [(str(package / name), 'pymessage/' + name) for name in ('data', 'web')]
datas += [(str(package / 'bin/manifest.json'), 'pymessage/bin'),
          (str(root / 'THIRD_PARTY_NOTICES.md'), 'notices'),
          (str(Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'), 'notices/python')]
datas += collect_data_files('wordcloud', includes=['*.ttf', 'stopwords'])
for dist in distributions():
    for entry in dist.files or []:
        if '.dist-info/' in str(entry) and any(term in entry.name.lower() for term in ('license', 'copying', 'notice')):
            datas.append((str(dist.locate_file(entry)), str(Path('notices') / entry.parent)))

a = Analysis([str(root / 'packaging/macos/desktop.py')], pathex=[str(root)],
             binaries=[(str(package / 'bin' / ('pymessage-decoder-' + arch)), 'pymessage/bin')],
             datas=datas, hookspath=[], hooksconfig={'matplotlib': {'backends': ['Agg']}},
             excludes=['pandas', 'pytest', 'IPython'])
exe = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name='Py-Message',
          console=False, target_arch=arch, upx=False)
files = COLLECT(exe, a.binaries, a.datas, name='runtime', upx=False)
app = BUNDLE(files, name='Py-Message.app', bundle_identifier='io.github.abcoding.pymessage',
             version=__version__, info_plist={
                 'CFBundleShortVersionString': __version__,
                 'LSMinimumSystemVersion': '14.0',
                 'NSHighResolutionCapable': True,
                 'NSAppleEventsUsageDescription': 'Match message addresses with contact names when you choose Match contacts.',
                 'NSContactsUsageDescription': 'Match message addresses with contact names when you choose Match contacts.',
             })
