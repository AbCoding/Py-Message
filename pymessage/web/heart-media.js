'use strict';

function cleanupHeartMedia() {
  const media = state.heartMedia;
  if (!media) return;
  media.stopped = true; media.abort.abort(); media.observer?.disconnect(); media.queue = [];
  for (const url of media.urls) URL.revokeObjectURL(url);
  state.heartMedia = null;
}
function heartMediaSession() {
  if (state.heartMedia) return state.heartMedia;
  const media = {abort: new AbortController(), urls: new Set(), queue: [], active: 0, stopped: false, observer: null};
  if (typeof IntersectionObserver === 'function') media.observer = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      media.observer.unobserve(entry.target);
      queueHeartMedia(media, entry.target.loadHeartMedia);
    }
  }, {rootMargin: '200px'});
  state.heartMedia = media; return media;
}
function queueHeartMedia(media, load) {
  if (media.stopped) return;
  media.queue.push(load);
  drainHeartMedia(media);
}
function drainHeartMedia(media) {
  while (!media.stopped && media.active < 2 && media.queue.length) {
    const load = media.queue.shift(); media.active++;
    Promise.resolve().then(() => media.stopped ? undefined : load()).catch(() => {}).finally(() => { media.active--; drainHeartMedia(media); });
  }
}
function observeHeartMedia(media, target, load) {
  if (media.observer) { target.loadHeartMedia = load; media.observer.observe(target); }
  else {
    const button = node('button', 'text-button', 'Load attachments'); button.type = 'button';
    button.addEventListener('click', () => { button.remove(); queueHeartMedia(media, load); }); target.append(button);
  }
}
async function heartMediaBlob(media, path, mime) {
  const response = await api(path, undefined, {signal: media.abort.signal});
  const blob = await response.blob();
  if (media.stopped) return null;
  const url = URL.createObjectURL(mime ? new Blob([blob], {type: mime}) : blob);
  media.urls.add(url); return url;
}
function appendHeartAttachments(parent, message, chatId) {
  const media = heartMediaSession(), target = node('div', 'heart-attachments');
  parent.append(target);
  const query = new URLSearchParams({chat_id: chatId, message_id: message.id});
  const status = node('p', 'card-note', 'Attachments load when visible.'); target.append(status);
  observeHeartMedia(media, target, async () => {
    try {
      const data = await (await api('/api/leaderboard-attachments?' + query, undefined, {signal: media.abort.signal})).json();
      if (media.stopped) return;
      target.replaceChildren();
      if (!data.attachments.length) target.append(node('p', 'card-note', data.note || 'No attachment files are recorded.'));
      for (const attachment of data.attachments) renderHeartAttachment(target, media, query, attachment);
    } catch (error) {
      if (!media.stopped) status.textContent = 'Attachments could not be loaded: ' + error.message;
    }
  });
}
function renderHeartAttachment(target, media, baseQuery, item) {
  const card = node('div', 'heart-attachment'); target.append(card);
  card.append(node('div', 'heart-attachment-name', item.name));
  if (!item.available) {
    card.append(node('p', 'card-note', 'Not available locally. The file may be missing, stored only in iCloud, or blocked by macOS permissions.'));
    return;
  }
  const query = new URLSearchParams(baseQuery); query.set('attachment_id', item.id);
  const path = '/api/leaderboard-attachment?' + query;
  const status = node('p', 'card-note');
  const download = node('button', 'text-button', 'Download original' + (item.size == null ? '' : ' · ' + (item.size / 1024 / 1024).toFixed(1) + ' MB'));
  download.type = 'button'; card.append(download, status);
  download.addEventListener('click', async () => {
    if (media.stopped) return;
    download.disabled = true; status.textContent = 'Downloading…';
    try {
      const url = await heartMediaBlob(media, path);
      if (!url) return;
      const link = node('a'); link.href = url; link.download = item.name; link.click();
      status.textContent = '';
      setTimeout(() => { URL.revokeObjectURL(url); media.urls.delete(url); }, 60000);
    } catch (error) { if (!media.stopped) status.textContent = error.message; }
    finally { download.disabled = false; }
  });
  if (item.kind === 'image') {
    const preview = node('div', 'heart-image-preview'); card.append(preview);
    const note = node('p', 'card-note', 'Loading image preview…'); preview.append(note);
    observeHeartMedia(media, preview, async () => {
      try {
        const url = await heartMediaBlob(media, path + '&preview=1'); if (!url) return;
        const image = node('img'); image.alt = item.name; image.decoding = 'async'; image.src = url;
        image.addEventListener('error', () => { preview.replaceChildren(node('p', 'card-note', 'Preview unavailable. Download the original to view it.')); });
        preview.replaceChildren(image);
      } catch (error) { if (!media.stopped) note.textContent = error.message; }
    });
  } else if (item.kind === 'video' || item.kind === 'audio') {
    if (item.size > 64 * 1024 * 1024) { status.textContent = 'Large media file. Download the original to play it.'; return; }
    const load = node('button', 'text-button', 'Load ' + item.kind); load.type = 'button'; card.append(load);
    load.addEventListener('click', () => {
      if (media.stopped || load.disabled) return;
      load.disabled = true; status.textContent = 'Loading ' + item.kind + '…';
      queueHeartMedia(media, async () => {
        try {
          const url = await heartMediaBlob(media, path, item.mime); if (!url) return;
          const player = node(item.kind); player.controls = true; player.preload = 'metadata'; player.src = url;
          player.setAttribute('aria-label', item.name);
          player.addEventListener('error', () => { status.textContent = 'This media format cannot play here. Download the original to open it.'; });
          card.append(player); load.remove(); status.textContent = '';
        } catch (error) { if (!media.stopped) { status.textContent = error.message; load.disabled = false; } }
      });
    });
  } else status.textContent = 'Download this file to open it.';
}
