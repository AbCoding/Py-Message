# Build a Mac release

Use macOS 14 or later and Python 3.10 with Tcl/Tk. Build each architecture in its own virtual environment with native wheels. On an Apple Silicon host with Rosetta and a universal python.org interpreter, prefix each Python command with `arch -arm64` or `arch -x86_64`. Use separate environment paths for each architecture.

From the repository root:

```sh
python3.10 -m venv build/macos/release-env
build/macos/release-env/bin/python -m pip install --only-binary=:all: -c requirements-lock.txt pyinstaller==6.22.3 beautifulsoup4 matplotlib wordcloud tzdata
build/macos/release-env/bin/python packaging/macos/build.py
```

The archive is under `dist/macos/arm64/` or `dist/macos/x86_64/`. The build verifies the source decoder, collects the app resources and licenses, refreshes the bundled decoder checksum after Mach-O relocation, and verifies the final ad-hoc signature. Each zip contains one Finder app. Test the zip on a Mac with a desktop session before uploading:

```sh
python3 packaging/macos/smoke.py dist/macos/arm64/Py-Message-Apple-Silicon.zip
python3 packaging/macos/smoke.py dist/macos/x86_64/Py-Message-Intel.zip
```

The smoke check uses a temporary home and a synthetic Messages database. It exercises the included runtime, desktop window, browser handoff, loopback API, and chart rendering.
