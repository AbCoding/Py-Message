import base64
from datetime import datetime
import plistlib
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


def encode(value):
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, datetime):
        return {'$datetime':value.isoformat()}
    if isinstance(value, plistlib.UID):
        return {'$uid':value.data}
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=encode)


def digest(value):
    return hashlib.sha256((value if isinstance(value, bytes) else dumps(value).encode('utf-8'))).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode('utf-8') if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def atomic_output(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.stem, suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    try:
        yield Path(temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another Py-Message process is using this archive. Retry when it finishes.') from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def slug(name, identity):
    label = re.sub(r'[^\w.-]+', '-', str(name), flags=re.UNICODE).strip('.-')[:70] or 'conversation'
    return label + '-' + digest(identity)[:12]


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


class Timings:
    def __init__(self):
        self.values = {}

    @contextmanager
    def stage(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.values[name] = round(time.perf_counter() - start, 6)
