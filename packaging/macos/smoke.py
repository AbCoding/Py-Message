"""Test the shipped zip in an isolated home, including Finder's no-argument launch path."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.synthetic import create_database


def check(archive, home):
    subprocess.run(['ditto', '-x', '-k', str(archive), str(home)], check=True)
    app = home / 'Py-Message.app'
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    executable = app / 'Contents/MacOS/Py-Message'
    database = create_database(home / 'fixture.db', count=24)
    output = home / 'Library/Application Support/Py-Message'
    # Capture the browser handoff locally without opening a personal browser profile.
    browser = home / 'capture-browser'
    browser.write_text('#!/bin/sh\nprintf "%s" "$1" > "$HOME/workspace-url"\n')
    browser.chmod(0o700)
    env = dict(os.environ, HOME=str(home), BROWSER=str(browser), TZ='UTC', MPLCONFIGDIR=str(home / 'mpl'))
    env.pop('PYTHONPATH', None)
    for args in (['--prepare-exporter'], ['sync', '--db-path', str(database), '--output', str(output)]):
        subprocess.run([str(executable), *args], cwd=home, env=env, check=True, timeout=60)
    process = subprocess.Popen([str(executable)], cwd=home, env=env)
    try:
        handoff = home / 'workspace-url'
        for _ in range(300):
            if handoff.is_file() and handoff.stat().st_size:
                break
            if process.poll() is not None:
                raise RuntimeError('The app exited before opening its workspace.')
            time.sleep(0.1)
        url = urlsplit(handoff.read_text())
        assert url.hostname == '127.0.0.1' and url.fragment
        origin = f'http://{url.netloc}'
        def fetch(path, body=None):
            request = Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                              headers={'Authorization':'Bearer ' + url.fragment, 'Content-Type':'application/json'})
            with urlopen(request, timeout=10) as response:
                return response.read()
        assert b'data-theme="paper"' in fetch('/')
        assert len(fetch('/emoji-picker.json')) > 1000
        chat = json.loads(fetch('/api/chats'))['chats'][0]
        assert chat['count'] == 24
        json.loads(fetch('/api/analyze', {'chat_id':chat['id'], 'group_by':'month'}))
        for _ in range(300):
            job = json.loads(fetch('/api/status'))['job']
            if job['state'] != 'running':
                break
            time.sleep(0.2)
        assert job['state'] == 'complete', job.get('error', job['state'])
        report = job['result']
        assert report['metrics']['message_count'] == 24
        assert fetch(report['artifacts']['wordcloud']).startswith(b'\x89PNG')
    except OSError as error:
        time.sleep(0.5)
        raise RuntimeError(f'Workspace connection failed (app exit status {process.poll()}).') from error
    finally:
        process.terminate()
        process.wait(timeout=10)
    print('Verified extracted app, decoder checksum, import, desktop launch, web assets, and rendered word cloud.', flush=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='pymessage-release-') as directory:
        check(Path(sys.argv[1]).resolve(), Path(directory))
