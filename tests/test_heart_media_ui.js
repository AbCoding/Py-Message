// Fictional attachments only: verify lazy requests, playback, and navigation cleanup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.listeners = {}; }
  append(...children) { this.children.push(...children); for (const child of children) child.parent = this; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  click() { return this.listeners.click?.(); }
  set innerHTML(_) { throw Error('Unsafe HTML'); }
}
class Observer {
  constructor(callback) { this.callback = callback; this.targets = new Set(); }
  observe(target) { this.targets.add(target); }
  unobserve(target) { this.targets.delete(target); }
  disconnect() { this.targets.clear(); }
  reveal(target) { assert(this.targets.has(target)); this.callback([{target, isIntersecting: true}]); }
}
const requests = [], revoked = [], state = {};
let created = 0;
const context = vm.createContext({state, AbortController, Blob, URLSearchParams, IntersectionObserver: Observer,
  node: (tag, css, text) => new Element(tag, text),
  URL: {createObjectURL() { return 'blob:fixture-' + ++created; }, revokeObjectURL(url) { revoked.push(url); }},
  setTimeout: () => {},
  api: async (path, payload, options) => {
    requests.push({path, options});
    return {json: async () => ({attachments: [
      {id: 'image', name: '<img onerror=bad>.png', kind: 'image', available: true, size: 100},
      {id: 'missing', name: 'missing.jpg', kind: 'image', available: false},
      {id: 'movie', name: 'movie.mp4', kind: 'video', mime: 'video/mp4', available: true, size: 100},
      {id: 'large', name: 'large.mp4', kind: 'video', available: true, size: 100 * 1024 * 1024},
    ]}), blob: async () => new Blob(['fictional'])};
  },
});
vm.runInContext(fs.readFileSync(__dirname + '/../pymessage/web/heart-media.js', 'utf8'), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const parent = new Element('li');
  context.appendHeartAttachments(parent, {id: 'message & 1'}, 'group');
  assert.equal(requests.length, 0, 'No requests before scrolling to the message');
  const media = state.heartMedia, target = parent.children[0];
  media.observer.reveal(target); await tick();
  assert.equal(requests.length, 1);
  assert.equal(new URL('http://localhost' + requests[0].path).searchParams.get('message_id'), 'message & 1');
  const [picture, missing, movie, large] = target.children;
  assert.equal(picture.children[0].textContent, '<img onerror=bad>.png');
  assert.match(missing.children[1].textContent, /Not available locally/);
  assert.equal(missing.children.some(child => child.tag === 'button'), false);
  assert.equal(large.children.filter(child => child.tag === 'button').length, 1, 'Large media only offers download');
  const preview = picture.children.at(-1);
  media.observer.reveal(preview); await tick();
  assert.equal(preview.children[0].tag, 'img');
  assert.match(requests[1].path, /preview=1/);
  assert.equal(movie.children.some(child => child.tag === 'video'), false);
  movie.children.find(child => child.textContent === 'Load video').click(); await tick();
  const player = movie.children.find(child => child.tag === 'video');
  assert.equal(player.controls, true); assert.equal(player.autoplay, undefined);
  await picture.children.find(child => child.tag === 'button').click();
  assert(!requests.at(-1).path.includes('preview=1'), 'Download fetches the original');
  const urls = [...media.urls];
  context.cleanupHeartMedia();
  assert.equal(state.heartMedia, null); assert.equal(media.observer.targets.size, 0);
  for (const request of requests) assert.equal(request.options.signal.aborted, true);
  for (const url of urls) assert(revoked.includes(url));

  // Never start queued requests after leaving the leaderboard.
  const next = context.heartMediaSession(); let running = 0, maxRunning = 0; const release = [];
  for (let i = 0; i < 5; i++) context.queueHeartMedia(next, () => {
    running++; maxRunning = Math.max(maxRunning, running);
    return new Promise(resolve => release.push(() => { running--; resolve(); }));
  });
  await tick(); assert.equal(maxRunning, 2); assert.equal(next.queue.length, 3);
  context.cleanupHeartMedia(); release.forEach(resolve => resolve()); await tick();
  assert.equal(running, 0); assert.equal(next.active, 0);
  console.log('Leaderboard attachment UI checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
