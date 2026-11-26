"""Bounded local previews for attachments already linked to leaderboard messages."""
import json
import mimetypes
from pathlib import Path
import platform
import subprocess
import tempfile

from .util import digest

IMAGE_TYPES = {'image/jpeg','image/png','image/gif','image/webp','image/heic','image/heif','image/tiff','image/bmp'}
VIDEO_TYPES = {'video/mp4','video/quicktime','video/webm','video/x-m4v'}
AUDIO_TYPES = {'audio/mpeg','audio/mp4','audio/x-m4a','audio/wav','audio/x-wav','audio/ogg','audio/aac','audio/x-caf'}


def describe(row, roots):
    metadata = json.loads(row['metadata'])
    name = str(metadata.get('transfer_name') or Path(row['path'] or '').name or 'Attachment').replace('\\','/').rsplit('/',1)[-1]
    name = ''.join(c for c in name if c.isprintable())[:240] or 'Attachment'
    mime = metadata.get('mime_type') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    mime = str(mime).split(';',1)[0].lower()
    if Path(name).suffix.lower() in ('.heic','.heif'): mime = 'image/heic'
    kind = 'image' if mime in IMAGE_TYPES else 'video' if mime in VIDEO_TYPES else 'audio' if mime in AUDIO_TYPES else 'file'
    resolved = None
    for candidate in (row['archived_path'],row['path']):
        if not candidate: continue
        try:
            path = Path(candidate).expanduser().resolve()
            if any(path.is_relative_to(root.expanduser().resolve()) for root in roots) and path.is_file():
                with path.open('rb'): pass
                resolved = path
                break
        except OSError:
            continue
    return {'id':row['id'],'name':name,'mime':mime,'kind':kind,
            'available':resolved is not None,'size':resolved.stat().st_size if resolved else None},resolved


def image_preview(path, mime, directory):
    from PIL import Image, ImageOps
    stamp = path.stat()
    if stamp.st_size>32*1024*1024:
        raise ValueError('This image is too large for an inline preview. Download the original instead.')
    key = digest(['preview-1',str(path),stamp.st_size,stamp.st_mtime_ns])
    directory.mkdir(parents=True,exist_ok=True)
    target = directory/(key+'.png')
    if target.is_file(): return target,'image/png'
    with tempfile.TemporaryDirectory(prefix='preview-',dir=directory) as temp:
        source = path
        if mime in ('image/heic','image/heif') and platform.system()=='Darwin':
            source = Path(temp)/'converted.png'
            try:
                subprocess.run(['/usr/bin/sips','-s','format','png',str(path),'--out',str(source)],
                               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            except (OSError,subprocess.SubprocessError) as error:
                raise ValueError('Image preview could not be converted. Download the original instead.') from error
        try:
            with Image.open(source) as picture:
                if picture.width*picture.height>80_000_000:
                    raise ValueError('Image dimensions are too large for a preview. Download the original instead.')
                if picture.format=='GIF' and mime=='image/gif': return path,'image/gif'
                picture.thumbnail((1280,1280))
                output = ImageOps.exif_transpose(picture).convert('RGBA')
                temporary = Path(temp)/'preview.png'
                output.save(temporary,'PNG')
                temporary.chmod(0o600)
                temporary.replace(target)
        except (OSError,Image.DecompressionBombError) as error:
            raise ValueError('Image preview is unavailable. Download the original instead.') from error
    return target,'image/png'
