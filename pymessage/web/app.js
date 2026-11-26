'use strict';

const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('pymessage-token') || '';
if (location.hash) {
  sessionStorage.setItem('pymessage-token', token);
  history.replaceState(null, '', location.pathname);
}
const state = {
  chats: [], chat: null, search: '', chatRequest: 0, messageRequest: 0,
  messageOffset: 0, messages: [], timezone: 'UTC', busy: false,
  result: null, results: new Map(), cloudURL: null, renderVersion: 0,
  contactLabels: {}, demo: false,
  libraryMode: 'conversations', personScope: 'direct', linkRequest: 0, linkSource: null,
  activity: null, activityHidden: new Set(), activityIndex: 0, activityCursor: null,
  semantic: null, semanticSelected: new Map(), semanticSearch: 0,
  wordContext: null, contextSelected: new Map(), contextSearch: 0, contextWord: '',
  hearts: null,
};
const colors = Array.from({length:5}, (_, index) => 'var(--series-' + (index + 1) + ')');
const number = value => Number(value || 0).toLocaleString();
function displaySender(sender) {
  if (sender === 'Me') return 'You';
  return Object.prototype.hasOwnProperty.call(state.contactLabels, sender) ? state.contactLabels[sender] : sender;
}
function contactStatus(contacts) {
  $('contacts-summary').textContent = contacts.updated ? number(contacts.count) + ' contacts · saved locally' : 'Label numbers with names';
  $('match-contacts').textContent = contacts.updated ? 'Refresh contacts' : 'Match contacts';
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function notify(message, error = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = !message;
  $('sidebar-status').textContent = message;
  $('sidebar-status').classList.toggle('error', error);
  $('sidebar-status').hidden = !message;
}
async function api(path, payload, options = {}) {
  const response = await fetch(path, {
    signal: options.signal,
    headers: {Authorization: 'Bearer ' + token, ...(payload === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(payload === undefined ? {} : {method: 'POST', body: JSON.stringify(payload)}),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.error || 'Could not connect to the local app. Check that it is still running.');
    error.status = response.status; throw error;
  }
  return response;
}
async function json(path, payload) { return (await api(path, payload)).json(); }
function safe(action) {
  return (...args) => Promise.resolve().then(() => action(...args)).catch(error => notify(error.message, true));
}
function dateLabel(value, options = {}) {
  if (!value) return 'Unknown date';
  return new Intl.DateTimeFormat(undefined, {timeZone: state.timezone, ...options}).format(new Date(value));
}
function initials(name) {
  return (name || '?').split(/[\s@._+-]+/).filter(Boolean).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase();
}
function avatar(element, chat) {
  let hash = 0;
  for (const char of chat.id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const tone = hash % 5 + 1;
  element.style.background = 'var(--avatar-bg-' + tone + ')';
  element.style.color = 'var(--avatar-ink-' + tone + ')';
  element.textContent = chat.kind !== 'person' && chat.participants.length > 1 ? '•••' : initials(chat.name);
}
function setBusy(value, kind = '') {
  state.busy = value;
  for (const control of $('explore-form').elements) control.disabled = value;
  $('work-apply').disabled = value;
  $('sync').disabled = value;
  $('load').disabled = value;
  $('match-contacts').disabled = value || state.demo;
  $('link-person').disabled = value;
  $('unlink-person').disabled = value;
  for (const control of $('analysis-form').elements) control.disabled = value;
  for (const control of $('activity-form').elements) control.disabled = value;
  for (const control of $('semantic-form').elements) control.disabled = value;
  for (const control of $('context-form').elements) control.disabled = value;
  $('context-run').textContent = value && kind === 'word-context' ? 'Analyzing…' : 'Analyze words';
  $('semantic-run').textContent = value && kind === 'semantic' ? 'Analyzing…' : 'Analyze vocabulary';
  for (const control of $('semantic-words').querySelectorAll('button')) control.disabled = value;
  $('activity-run').textContent = value && kind === 'activity' ? 'Counting…' : 'Plot';
  $('run-analysis').textContent = value && kind === 'analyze' ? 'Analyzing…' : 'Run analysis';
  $('load').textContent = value && kind === 'sync' ? 'Loading new data…' : 'Load conversations';
  $('sync').textContent = value && kind === 'sync' ? 'Loading…' : 'Load new data';
}
function renderChats() {
  const list = $('chat-list');
  list.replaceChildren();
  for (const chat of state.chats) {
    const button = node('button', 'chat' + ((state.chat?.id || work.route?.query.target) === chat.id ? ' selected' : ''));
    button.setAttribute('aria-pressed', String((state.chat?.id || work.route?.query.target) === chat.id));
    const face = node('span', 'avatar'); avatar(face, chat);
    const copy = node('span', 'chat-copy');
    const line = node('span', 'chat-line');
    line.append(node('span', 'chat-name', chat.name || 'Conversation'));
    if (chat.kind !== 'person') line.append(node('span', 'chat-date', dateLabel(chat.latest, {month: 'short', day: 'numeric'})));
    copy.append(line, node('span', 'chat-preview', (chat.from_me ? 'You: ' : '') + chat.preview));
    button.append(face, copy);
    button.addEventListener('click', safe(() => selectWorkEntry(chat)));
    list.append(button);
  }
  if (!state.chats.length) {
    const empty = node('div', 'empty-list', state.search ? 'No matches.' : work.ready ? 'No conversations in this selection.' : 'Your conversations will appear here.');
    list.append(empty);
  }
  $('chat-count').textContent = state.chats.length ? number(state.chats.length) + ($('more-chats').hidden ? '' : '+') : '';
}
async function loadChats(append = false) {
  const request = ++state.chatRequest;
  const offset = append ? state.chats.length : 0;
  const people = state.libraryMode === 'people';
  const data = await json((people ? '/api/people?' : '/api/chats?') + new URLSearchParams({q: state.search, offset, scope:state.listPage === 'overview' ? '' : state.listPage}));
  if (request !== state.chatRequest) return;
  const entries = people ? data.people.map(personEntry) : data.chats;
  state.chats = append ? [...state.chats, ...entries] : entries;
  $('people-hint').hidden = !people || data.identity_ready;
  $('people-hint').textContent = 'Refresh contacts to group phone numbers and emails from the same contact. You can also link addresses manually.';
  $('more-chats').hidden = !data.has_more;
  renderChats();
}
function showTab(tab) {
  const insights = tab === 'analysis' && state.chat?.kind !== 'person';
  $('messages-view').hidden = insights;
  $('analysis-view').hidden = !insights;
  for (const [id, active] of [['messages-tab', !insights], ['analysis-tab', insights]]) {
    $(id).classList.toggle('active', active);
    $(id).setAttribute('aria-pressed', String(active));
  }
}
async function selectChat(chat) {
  cleanupHeartMedia();
  $('explorer').hidden = true;
  $('context-view').hidden = true;
  $('semantic-view').hidden = true;
  $('people-activity').hidden = true;
  if (state.chat?.id !== chat.id) state.personScope = chat.kind === 'person' && !chat.direct_count ? 'groups' : 'direct';
  state.chat = chat;
  state.messageOffset = 0;
  state.messages = [];
  state.result = null;
  state.renderVersion++;
  $('messages').replaceChildren(node('p', 'empty-list', 'Loading messages…'));
  $('older').hidden = true;
  $('welcome').hidden = true;
  $('conversation').hidden = false;
  document.querySelector('.app-shell').classList.add('has-chat');
  $('chat-title').textContent = chat.name || 'Conversation';
  const person = chat.kind === 'person';
  $('chat-detail').textContent = person ? chat.preview : number(chat.count) + ' messages · ' + (chat.participants.length > 1 ? chat.participants.map(displaySender).join(', ') + ' + you' : chat.participants[0] || 'Local archive');
  $('chat-detail').title = chat.participants.join(', ');
  avatar($('header-avatar'), chat);
  $('results').hidden = true;
  $('analysis-empty').hidden = false;
  $('person-controls').hidden = !person;
  $('analysis-tab').hidden = person;
  $('messages-tab').textContent = person ? 'Messages' : 'Conversation';
  document.querySelector('.conversation-bottom').hidden = person;
  if (person) {
    $('person-threads').replaceChildren();
    $('person-addresses').textContent = chat.addresses.join(' · ');
    $('person-scope').value = state.personScope;
    $('unlink-person').hidden = !chat.manual_links;
    personNote();
  }
  renderChats();
  showTab('messages');
  const saved = person ? null : state.results.get(chat.id);
  if (saved) {
    restoreFilters(saved.config);
    await renderResults(saved);
  }
  if (state.chat?.id !== chat.id) return;
  if (person) {
    const detail = await json('/api/person?' + new URLSearchParams({id: chat.id}));
    if (state.chat?.id !== chat.id) return;
    state.chat = personEntry(detail);
    $('chat-detail').textContent = state.chat.preview;
    renderPersonThreads(detail);
  }
  await loadMessages(false);
}
async function loadMessages(older) {
  if (!state.chat) return;
  const request = ++state.messageRequest;
  const chatId = state.chat.id;
  const offset = older ? state.messageOffset : 0;
  $('older').disabled = true;
  try {
    const person = state.chat.kind === 'person';
    const data = await json(person ? '/api/person/messages?' + new URLSearchParams({id: chatId, scope: state.personScope, offset}) : '/api/messages?' + new URLSearchParams({chat_id: chatId, offset}));
    if (request !== state.messageRequest || state.chat?.id !== chatId) return;
    const scroll = $('message-scroll');
    const previousHeight = scroll.scrollHeight;
    const previousTop = scroll.scrollTop;
    state.messages = older ? [...data.messages, ...state.messages] : data.messages;
    state.messageOffset = offset + data.messages.length;
    renderMessages();
    $('older').hidden = !data.has_more;
    scroll.scrollTop = older ? scroll.scrollHeight - previousHeight + previousTop : scroll.scrollHeight;
  } finally {
    if (request === state.messageRequest) $('older').disabled = false;
  }
}
function renderMessages() {
  const target = $('messages');
  target.replaceChildren();
  let lastDate = null;
  for (const message of state.messages) {
    const day = dateLabel(message.timestamp, {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'});
    if (day !== lastDate) {
      target.append(node('div', 'date-separator', day)); lastDate = day;
    }
    if (message.kind !== 'message') {
      target.append(node('div', 'message-event', (message.kind === 'reaction' ? 'Reaction event' : 'Conversation event') + (message.text ? ' · ' + message.text : '')));
      continue;
    }
    const row = node('div', 'message-row' + (message.is_from_me ? ' mine' : ''));
    if (!message.is_from_me && (state.chat.kind === 'person' || state.chat.participants.length > 1)) row.append(node('div', 'message-sender', displaySender(message.sender)));
    if (message.origins?.length) {
      const origins = node('div', 'message-origins');
      for (const origin of message.origins) {
        const button = node('button', 'text-button', origin.name || 'Conversation');
        button.title = 'Open source conversation' + (origin.addresses?.length ? ' · ' + origin.addresses.join(', ') : '');
        button.addEventListener('click', safe(() => openOrigin(origin.id)));
        origins.append(button);
      }
      row.append(origins);
    }
    const content = message.unsent ? 'Message unsent' : message.text || (message.decode_status === 'unsupported' ? 'Message could not be decoded' : message.attachments ? 'Attachment' : 'Non-text message');
    row.append(node('div', 'bubble', content));
    const details = [];
    if (message.timestamp) details.push(dateLabel(message.timestamp, {hour: 'numeric', minute: '2-digit'}));
    if (message.edited) details.push('Edited');
    if (message.attachments) details.push(number(message.attachments) + ' attachment' + (message.attachments === 1 ? '' : 's'));
    if (message.decode_status === 'unsupported' && message.text) details.push('Partial decoding');
    row.append(node('div', 'message-time', details.join(' · ')));
    target.append(row);
  }
  if (!state.messages.length) target.append(node('p', 'empty-list', state.chat.kind === 'person' ? 'No messages in this view.' : 'No current messages in this conversation.'));
}
function personEntry(person) {
  return {...person, kind: 'person', participants: person.addresses,
    preview: number(person.direct_count) + ' direct · ' + number(person.group_count) + ' group conversations'};
}
function personNote() {
  $('person-note').textContent = state.personScope === 'direct'
    ? 'Your messages and theirs across linked direct conversations.'
    : 'Only messages sent by this person in groups. Your group messages are not assigned to individual recipients.';
}
function clearSelection() {
  cleanupHeartMedia();
  $('explorer').hidden = true;
  $('context-view').hidden = true;
  $('semantic-view').hidden = true;
  state.chat = null;
  state.messages = [];
  state.messageRequest++;
  state.renderVersion++;
  $('conversation').hidden = true;
  $('welcome').hidden = false;
  $('people-activity').hidden = true;
  document.querySelector('.app-shell').classList.remove('has-chat');
}
async function setLibraryMode(mode) {
  clearTimeout(searchTimer);
  state.libraryMode = mode;
  state.search = '';
  $('search').value = '';
  $('search').placeholder = mode === 'people' ? 'Search people or addresses' : 'Search conversations';
  $('search').setAttribute('aria-label', $('search').placeholder);
  $('library-label').textContent = mode === 'people' ? 'PEOPLE' : 'CONVERSATIONS';
  $('more-chats').textContent = mode === 'people' ? 'More people' : 'More conversations';
  clearSelection();
  state.chats = [];
  $('more-chats').hidden = true;
  $('chat-list').replaceChildren(node('p', 'empty-list', 'Loading…'));
  await loadChats();
}
async function openOrigin(chatId) {
  const chat = await json('/api/chat?' + new URLSearchParams({id: chatId}));
  const page = chat.scope === 'groups' ? 'groups' : 'direct';
  await navigateWork({...baseQuery(page), ...inheritedRange(), target:page === 'groups' ? chat.id : ''}, 'messages', true, chat);
}
function renderPersonThreads(person) {
  $('person-addresses').textContent = person.addresses.join(' · ') || 'No address recorded';
  $('unlink-person').hidden = !person.manual_links;
  $('person-threads').replaceChildren(...person.threads.map(thread => {
    const button = node('button', 'text-button', (thread.name || 'Conversation') + (thread.scope === 'direct' ? ' · Direct' : ' · Group'));
    button.title = thread.addresses.join(', ');
    button.addEventListener('click', safe(() => openOrigin(thread.id)));
    return button;
  }));
}
async function linkCandidates() {
  const request = ++state.linkRequest;
  $('link-target').replaceChildren();
  $('link-submit').disabled = true;
  const result = await json('/api/people?' + new URLSearchParams({q: $('link-search').value}));
  if (request !== state.linkRequest || !$('link-dialog').open) return;
  for (const person of result.people) {
    if (person.id === state.linkSource) continue;
    const option = node('option', '', person.name + ' · ' + person.addresses.join(', '));
    option.value = person.id; $('link-target').append(option);
  }
  $('link-submit').disabled = !$('link-target').options.length;
}
async function openLinkDialog() {
  if (state.busy || state.chat?.kind !== 'person') return;
  state.linkSource = state.chat.id;
  $('link-description').textContent = 'Combine another entry with ' + state.chat.name + '.';
  $('link-search').value = '';
  $('link-error').hidden = true;
  $('link-dialog').showModal();
  try { await linkCandidates(); }
  catch (error) { $('link-error').textContent = error.message; $('link-error').hidden = false; }
}
async function submitLink() {
  if (!$('link-target').value) return;
  $('link-submit').disabled = true;
  try {
    const person = await json('/api/people/link', {person_id: state.linkSource, other_id: $('link-target').value});
    invalidateActivity(); invalidateSemantic(); invalidateContext();
    $('link-dialog').close();
    await loadChats();
    work.results.clear(); work.details.clear();
    await navigateWork({...baseQuery('direct'), target:person.id}, 'messages');
    notify('Addresses linked. Original conversations are unchanged.');
  } catch (error) {
    $('link-error').textContent = error.message;
    $('link-error').hidden = false;
  } finally { $('link-submit').disabled = false; }
}
async function unlinkPerson() {
  if (state.busy || state.chat?.kind !== 'person') return;
  $('unlink-person').disabled = true;
  try {
    const person = await json('/api/people/unlink', {person_id: state.chat.id});
    invalidateActivity(); invalidateSemantic(); invalidateContext();
    await loadChats();
    work.results.clear(); work.details.clear();
    await navigateWork({...baseQuery('direct'), target:person.id}, 'messages');
    notify('Manual links removed. Addresses on the same contact remain grouped.');
  } finally { $('unlink-person').disabled = state.busy; }
}
function restoreFilters(config) {
  $('start-date').value = config.start_date || '';
  $('end-date').value = config.end_date || '';
  $('group-by').value = config.group_by;
  $('common-words').checked = config.filter_common_words;
  $('by-sender').checked = config.separate_by_sender;
}
async function sync() {
  if (state.busy) return;
  await runJob('sync', {});
}
async function runAnalysis(event) {
  event.preventDefault();
  if (!state.chat || state.chat.kind === 'person' || state.busy) return;
  const payload = {
    chat_id: state.chat.id, timezone: state.timezone,
    start_date: $('start-date').value, end_date: $('end-date').value,
    group_by: $('group-by').value, filter_common_words: $('common-words').checked,
    separate_by_sender: $('by-sender').checked,
  };
  if (payload.start_date && payload.end_date && payload.start_date >= payload.end_date) throw new Error('Choose an end date after the start date. The end date is exclusive.');
  await runJob('analyze', payload);
}
async function runJob(kind, payload) {
  setBusy(true, kind);
  notify(kind === 'explore' ? 'Calculating archive trends…' : kind === 'inspect' ? 'Loading matching messages…' : kind === 'word-context' ? 'Comparing word context…' : kind === 'semantic' ? 'Analyzing your sent words…' : kind === 'activity' ? 'Counting messages across people…' : kind === 'contacts' ? 'Reading names from Contacts… Allow access if macOS asks.' : kind === 'sync' ? 'Importing messages…' : 'Analyzing conversation…');
  try {
    const job = await json('/api/' + kind, payload);
    return await watchJob(job.id);
  } finally { setBusy(false); }
}
async function watchJob(id) {
  for (;;) {
    const status = await json('/api/status');
    const job = status.job;
    if (job.id !== id) throw new Error('The active operation changed. Refresh to see its status.');
    if (job.state === 'failed') throw new Error(job.error);
    if (job.state === 'complete') {
      if (job.kind === 'sync') {
        invalidateActivity(); invalidateSemantic(); invalidateContext();
        state.results.clear();
        state.result = null;
        state.renderVersion++;
        $('results').hidden = true;
        $('analysis-empty').hidden = false;
        work.ready = true;
        work.counts = await json('/api/library-counts');
        work.results.clear(); work.details.clear();
        state.contactLabels = await json('/api/contact-labels');
        await navigateWork(baseQuery('overview'), '', false);
        rememberRoute();
        await loadChats();
        const coverage = job.result.coverage;
        notify(job.result.partial ? 'Loaded with partial coverage: ' + number(coverage.unsupported_bodies) + ' undecoded bodies, ' + number(coverage.invalid_timestamps) + ' invalid dates, ' + number(coverage.unresolved_import_errors) + ' unresolved import issues. Original records are retained.' : 'Conversations are up to date.');
      } else if (job.kind === 'explore') {
        acceptWorkResult(job.result);
        notify('');
      } else if (job.kind === 'inspect') {
        notify('');
      } else if (job.kind === 'contacts') {
        invalidateActivity(); invalidateSemantic(); invalidateContext();
        contactStatus(job.result);
        state.contactLabels = await json('/api/contact-labels');
        work.results.clear(); work.details.clear();
        await navigateWork({...baseQuery(work.route.query.page), ...inheritedRange()}, '', false);
        rememberRoute();
        await loadChats();
        notify('Contact names updated for ' + number(job.result.matched_addresses) + ' message addresses. Your messages were not re-imported.');
      } else if (job.kind === 'activity') {
        renderPeopleActivity(job.result);
        notify('People activity ready' + (job.result.cached ? ' · reused saved counts.' : '.'));
      } else if (job.kind === 'semantic') {
        renderSemantic(job.result);
        notify('Vocabulary analysis ready' + (job.result.cached ? ' · reused saved results.' : '.'));
      } else if (job.kind === 'word-context') {
        renderWordContext(job.result);
        notify('Word context ready' + (job.result.cached ? ' · reused saved results.' : '.'));
      } else {
        state.results.set(job.result.chat_id, job.result);
        if (state.chat?.id === job.result.chat_id) {
          restoreFilters(job.result.config);
          await renderResults(job.result);
        }
        notify('Analysis ready' + (job.result.cached ? ' · reused saved results.' : '.'));
      }
      return job.result;
    }
    if (['semantic', 'word-context'].includes(job.kind) && job.progress) notify(job.progress + '…');
    await new Promise(resolve => setTimeout(resolve, 800));
  }
}
function metric(label, value, note = '') {
  const card = node('div', 'stat');
  card.append(node('div', 'stat-label', label), node('div', 'stat-value', value));
  if (note) card.append(node('div', 'stat-note', note));
  return card;
}
function duration(seconds) {
  if (seconds == null) return 'Not available';
  if (seconds < 60) return Math.round(seconds) + ' sec';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min';
  return (seconds / 3600).toFixed(1) + ' hrs';
}
async function renderResults(result) {
  state.result = result;
  const version = ++state.renderVersion;
  const m = result.metrics;
  $('analysis-empty').hidden = true;
  $('results').hidden = false;
  $('result-context').textContent = (result.config.start_date || 'All history') + ' → ' + (result.config.end_date ? result.config.end_date + ' (exclusive)' : 'latest') + (result.cached ? ' · cached' : '');
  $('stats').replaceChildren(
    metric('Messages', number(m.message_count)),
    metric('Words', number(m.word_count)),
    metric('Average words per message', m.average_words_per_message == null ? '—' : m.average_words_per_message.toFixed(1)),
    metric('Attachments', number(m.attachments), number(m.reaction_events) + ' reaction events'),
  );
  renderReactions(m.reactions_received);
  renderHearts(m.reactions_received?.hearts);
  renderActivity(m.timelines[result.config.group_by] || {}, result.config.group_by);
  const words = Object.entries(m.word_frequencies).sort((a, b) => b[1] - a[1]);
  $('top-words').replaceChildren(...words.slice(0, 8).map(([word, count]) => {
    const chip = node('span', 'word-chip', word); chip.append(node('b', '', number(count))); return chip;
  }));
  $('sender-bars').replaceChildren();
  Object.entries(m.senders).sort((a, b) => b[1] - a[1]).forEach(([sender, count], i) => {
    const row = node('div', 'sender-row');
    const label = node('div', 'sender-label');
    const name = node('span', '', displaySender(sender)); name.title = sender;
    label.append(name, node('span', '', number(count) + ' · ' + (m.message_count ? Math.round(count / m.message_count * 100) : 0) + '%'));
    const track = node('div', 'sender-track'); const fill = node('div', 'sender-fill');
    fill.style.width = (m.message_count ? count / m.message_count * 100 : 0) + '%'; fill.style.background = colors[i % colors.length];
    track.append(fill); row.append(label, track); $('sender-bars').append(row);
  });
  $('response-value').textContent = duration(m.mean_turn_response_seconds);
  $('response-note').textContent = m.response_metric_available ? m.response_metric + ' ' + number(m.response_count) + ' turns measured.' : 'Response timing is available for two-person conversations. Group chats have no unambiguous reply direction.';
  const warnings = [];
  for (const [key, label] of [['unsupported','undecoded bodies'], ['invalid_timestamps','messages without valid dates'], ['missing_attachments','unavailable attachments'], ['duplicates','duplicate rows excluded'], ['fully_unsent_messages','unsent messages']]) {
    if (m[key]) warnings.push(number(m[key]) + ' ' + label);
  }
  $('coverage').textContent = warnings.length ? warnings.join(' · ') + '. Original source records are retained.' : 'Based on your current local archive. Reactions and conversation events are counted separately from messages.';
  if (state.cloudURL) { URL.revokeObjectURL(state.cloudURL); state.cloudURL = null; }
  $('word-cloud').replaceChildren(node('span', '', words.length ? 'Preparing word cloud…' : 'No words in this date range.'));
  if (result.artifacts.wordcloud) {
    try {
      const blob = await (await api(result.artifacts.wordcloud)).blob();
      if (version !== state.renderVersion) return;
      state.cloudURL = URL.createObjectURL(blob);
      const img = node('img'); img.src = state.cloudURL; img.alt = 'Word cloud of the most frequent words in this conversation';
      $('word-cloud').replaceChildren(img);
    } catch (error) {
      if (version === state.renderVersion) $('word-cloud').textContent = 'Word cloud unavailable. Word frequencies are shown below.';
    }
  }
}
function renderReactions(data) {
  const target = $('reaction-table');
  target.replaceChildren();
  $('reaction-total').textContent = '';
  $('reaction-note').textContent = '';
  if (!data || !data.available) {
    target.append(node('p', 'card-note', data ? 'Reaction details are unavailable for HTML imports.' : 'Run analysis again to calculate reaction totals.'));
    return;
  }
  const labels = {heart: '♥ Heart', like: '👍 Like', dislike: '👎 Dislike', laugh: 'Ha ha', emphasis: '!!', question: '?', sticker: 'Sticker', unknown_emoji: 'Unknown emoji'};
  const descriptions = {emphasis: 'Emphasis', question: 'Question', laugh: 'Laugh'};
  const table = node('table');
  const head = node('thead'); const header = node('tr');
  for (const type of ['author', ...data.types, 'total']) {
    const title = type === 'author' ? 'Message author' : type === 'total' ? 'Total' : labels[type] || type.replace(/^emoji:/, '') + ' (emoji)';
    const cell = node('th', '', title); cell.scope = 'col';
    cell.title = descriptions[type] || title;
    cell.setAttribute('aria-label', cell.title);
    header.append(cell);
  }
  head.append(header); table.append(head);
  const body = node('tbody');
  const rows = Object.entries(data.by_sender).sort((a, b) => b[1].total - a[1].total || a[0].localeCompare(b[0]));
  for (const [sender, values] of rows) {
    const row = node('tr'); const author = node('th', '', displaySender(sender));
    author.scope = 'row'; author.title = sender; row.append(author);
    for (const type of data.types) row.append(node('td', '', number(values.counts[type])));
    row.append(node('td', 'reaction-sum', number(values.total))); body.append(row);
  }
  table.append(body);
  if (rows.length) target.append(table);
  else target.append(node('p', 'card-note', 'No messages in this date range.'));
  $('reaction-total').textContent = number(data.total) + ' total';
  const notes = [data.definition];
  if (data.unresolved_events) notes.push(number(data.unresolved_events) + ' events could not be attributed reliably in this conversation.');
  if (data.excluded_sticker_events) notes.push(number(data.excluded_sticker_events) + ' legacy sticker overlays excluded.');
  $('reaction-note').textContent = notes.join(' ');
}
function heartSenderName(sender) {
  const label = displaySender(sender);
  const duplicates = (state.hearts.matrix?.people || Object.keys(state.hearts.by_sender)).filter(key => displaySender(key) === label).length;
  return duplicates > 1 ? label + ' · ' + sender : label;
}
function renderHearts(data) {
  cleanupHeartMedia();
  state.hearts = data || null;
  $('hearts-section').hidden = !data;
  if (!data) return;
  $('heart-summary').textContent = number(data.total) + ' hearts on ' + number(data.message_count) + ' messages';
  $('heart-definition').textContent = data.definition;
  $('heart-history-note').textContent = 'Grouped by message date · ' + data.group_by.replaceAll('_', ' ') + ' · ' + data.timezone + '. ' + (data.coarsened ? 'This long range is grouped by month to keep the chart readable. ' : '') + (data.undated_messages ? number(data.undated_messages) + ' hearted messages have no date and are excluded from the timeline.' : 'Periods with no hearts are shown as zero.');
  const selector = $('heart-exact'), previous = selector.value;
  selector.replaceChildren();
  for (const count of data.buckets) {
    const option = node('option', '', 'Exactly ' + count + (count === 1 ? ' heart' : ' hearts'));
    option.value = String(count); selector.append(option);
  }
  if (data.buckets.some(count => String(count) === previous)) selector.value = previous;
  selector.disabled = !data.buckets.length;
  semanticTable($('heart-buckets-table'), ['Message author', ...data.buckets.map(n => n + (n === 1 ? ' heart' : ' hearts')), 'Hearted messages', 'Hearts'],
    Object.entries(data.by_sender).sort((a, b) => b[1].hearts - a[1].hearts || a[0].localeCompare(b[0])).map(([sender, row]) => [heartSenderName(sender), ...data.buckets.map(n => number(row.buckets[n] || 0)), number(row.messages), number(row.hearts)]));
  const list = $('heart-top-messages'); list.replaceChildren();
  $('heart-top-empty').hidden = data.top_messages.length > 0;
  $('heart-top-count').textContent = data.message_count ? 'Top ' + data.top_messages.length + ' of ' + number(data.message_count) : '';
  for (const message of data.top_messages) {
    const item = node('li', 'heart-message');
    const heading = node('div', 'heart-message-heading');
    heading.append(node('strong', '', heartSenderName(message.sender)), node('span', 'heart-score', '♥ ' + number(message.hearts)));
    const timestamp = node('time', 'heart-message-date', dateLabel(message.timestamp, {year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}));
    if (message.timestamp) timestamp.dateTime = message.timestamp;
    const text = message.unsent ? 'Message was unsent.' : message.text ? message.text + (message.truncated ? '…' : '') : message.unsupported ? 'Message text could not be decoded.' : message.attachments ? 'Attachment-only message.' : 'No text content.';
    item.append(heading, timestamp, node('p', 'heart-message-text', text));
    if (message.attachments) {
      item.append(node('span', 'card-note', number(message.attachments) + (message.attachments === 1 ? ' attachment' : ' attachments')));
      if (!message.unsent && state.result?.chat_id) appendHeartAttachments(item, message, state.result.chat_id);
    }
    list.append(item);
  }
  drawHeartHistory(); drawHeartBuckets(); drawHeartMatrix();
}
function drawHeartMatrix() {
  const target = $('heart-matrix'); target.replaceChildren();
  $('heart-cell-count').textContent = 'Select a cell for counts.';
  const matrix = state.hearts?.matrix;
  if (!matrix) { target.append(node('p', 'card-note', 'Run analysis again to calculate heart percentages.')); return; }
  if (!matrix.people.length) { target.append(node('p', 'card-note', 'No messages in this date range.')); return; }
  const table = node('table'), head = node('thead'), header = node('tr');
  const corner = node('th', '', 'Heart giver ↓ / Author →'); corner.scope = 'col'; header.append(corner);
  for (const author of matrix.people) {
    const cell = node('th', '', heartSenderName(author)); cell.scope = 'col'; cell.title = author; header.append(cell);
  }
  head.append(header); table.append(head);
  const body = node('tbody');
  for (const actor of matrix.people) {
    const row = node('tr'), label = node('th', '', heartSenderName(actor)); label.scope = 'row'; label.title = actor; row.append(label);
    for (const author of matrix.people) {
      const total = matrix.message_counts[author] || 0, count = matrix.counts[actor]?.[author] || 0;
      const valid = actor !== author && total > 0, fraction = valid ? count / total : 0;
      const percent = (100 * fraction).toFixed(1) + '%';
      const cell = node('td', valid ? 'heart-matrix-cell' : 'heart-matrix-empty', valid ? percent : '—');
      cell.tabIndex = 0;
      cell.title = actor === author ? 'Self-reactions excluded.' : !total ? 'No messages from ' + heartSenderName(author) + ' in this date range.'
        : heartSenderName(actor) + ' hearted ' + number(count) + ' of ' + heartSenderName(author) + '’s ' + number(total) + ' messages (' + percent + ').';
      cell.setAttribute('aria-label', cell.title);
      for (const event of ['focus','click']) cell.addEventListener(event, () => { $('heart-cell-count').textContent = cell.title; });
      if (valid) {
        cell.style.backgroundColor = 'color-mix(in srgb, var(--heat-high) ' + (fraction * 100) + '%, var(--heat-low))';
        cell.style.color = 'var(--heat-ink)';
      }
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body); target.append(table);
}
function drawHeartHistory() {
  const data = state.hearts; if (!data) return;
  const series = $('heart-chart-mode').value === 'people'
    ? data.series.map(row => ({name: heartSenderName(row.sender), values: row.values}))
    : [{name: 'Hearts', values: data.period_totals}];
  semanticChart($('heart-history'), data.periods, series, 'hearts');
}
function drawHeartBuckets() {
  const data = state.hearts; if (!data) return;
  const count = $('heart-exact').value;
  const target = $('heart-buckets-chart'); target.replaceChildren();
  if (!count) { target.append(node('p', 'card-note', 'No messages with hearts in this date range.')); return; }
  const rows = Object.entries(data.by_sender).map(([sender, row]) => ({sender, count: row.buckets[count] || 0})).sort((a, b) => b.count - a.count || a.sender.localeCompare(b.sender));
  const maximum = Math.max(1, ...rows.map(row => row.count));
  for (const row of rows) {
    const container = node('div', 'sender-row');
    const label = node('div', 'sender-label');
    label.append(node('span', '', heartSenderName(row.sender)), node('span', '', number(row.count) + (row.count === 1 ? ' message' : ' messages')));
    const track = node('div', 'sender-track'), fill = node('div', 'sender-fill');
    fill.style.width = (100 * row.count / maximum) + '%'; fill.style.background = 'var(--heat-bar)';
    track.append(fill); container.append(label, track); target.append(container);
  }
}
$('heart-chart-mode').addEventListener('change', drawHeartHistory);
$('heart-exact').addEventListener('change', drawHeartBuckets);
function svgNode(tag, attrs = {}, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}
function renderActivity(buckets, group) {
  const entries = Object.entries(buckets).sort((a, b) => a[0].localeCompare(b[0]));
  const senders = [...new Set(entries.flatMap(([, values]) => Object.keys(values)))];
  $('legend').replaceChildren(...senders.map((sender, i) => {
    const label = node('span'); const swatch = node('i'); swatch.style.background = colors[i % colors.length];
    label.title = sender;
    label.append(swatch, document.createTextNode(displaySender(sender))); return label;
  }));
  const labels = {week: 'By week', day: 'By day', month: 'By month', day_of_week: 'By day of week', time_of_day: 'By hour of day'};
  $('chart-caption').textContent = labels[group];
  const target = $('activity-chart'); target.replaceChildren();
  if (!entries.length) { target.append(node('p', 'empty-list', 'No dated messages in this range.')); $('activity-note').textContent = ''; return; }
  // Bound the SVG size for multi-year daily histories, preserving total counts.
  const stride = Math.max(1, Math.ceil(entries.length / 90));
  const bins = [];
  for (let i = 0; i < entries.length; i += stride) {
    const slice = entries.slice(i, i + stride); const values = {};
    for (const [, counts] of slice) for (const [sender, count] of Object.entries(counts)) values[sender] = (values[sender] || 0) + count;
    bins.push({first: slice[0][0], last: slice[slice.length - 1][0], values});
  }
  const max = Math.max(1, ...bins.map(bin => Object.values(bin.values).reduce((a, b) => a + b, 0)));
  const width = 760, height = 180, left = 40, top = 10, bottom = 28, plotHeight = height - top - bottom, step = (width - left - 12) / bins.length;
  const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': labels[group] + ', messages by sender'});
  for (let i = 0; i <= 3; i++) {
    const y = top + plotHeight * i / 3;
    svg.append(svgNode('line', {x1: left, x2: width, y1: y, y2: y, stroke: 'var(--chart-grid)', 'stroke-dasharray': '3 4'}));
    svg.append(svgNode('text', {x: left - 10, y: y + 3, 'text-anchor': 'end', fill: 'var(--chart-axis)', 'font-size': 9}, number(Math.round(max * (1 - i / 3)))));
  }
  bins.forEach((bin, index) => {
    let y = top + plotHeight;
    senders.forEach((sender, i) => {
      const count = bin.values[sender] || 0, barHeight = count / max * plotHeight;
      y -= barHeight;
      const rect = svgNode('rect', {x: left + index * step + step * .2, y, width: Math.max(1, step * .6), height: barHeight, fill: colors[i % colors.length], rx: 2});
      rect.append(svgNode('title', {}, `${bin.first}${bin.first !== bin.last ? ' – ' + bin.last : ''} · ${displaySender(sender)}: ${number(count)}`)); svg.append(rect);
    });
    if (index % Math.max(1, Math.ceil(bins.length / 7)) === 0) {
      const label = group === 'day_of_week' ? bin.first.slice(2, 5) : bin.first;
      svg.append(svgNode('text', {x: left + (index + .5) * step, y: height - 5, 'text-anchor': 'middle', fill: 'var(--chart-axis)', 'font-size': 8}, label));
    }
  });
  target.append(svg);
  $('activity-note').textContent = (stride > 1 ? 'Each bar combines up to ' + stride + ' active periods. ' : '') + 'Periods without messages are omitted. Hover over a bar for counts.';
}
function download() {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2) + '\n'], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = node('a');
  link.href = url; link.download = 'pymessage-statistics.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
let searchTimer;
let linkSearchTimer;
$('person-scope').addEventListener('change', safe(async () => {
  state.personScope = $('person-scope').value;
  state.messages = [];
  $('messages').replaceChildren(node('p', 'empty-list', 'Loading messages…'));
  $('older').hidden = true;
  personNote();
  await loadMessages(false);
}));
$('link-person').addEventListener('click', safe(openLinkDialog));
$('unlink-person').addEventListener('click', safe(unlinkPerson));
$('link-cancel').addEventListener('click', () => $('link-dialog').close());
$('link-dialog').addEventListener('close', () => { state.linkRequest++; });
$('link-search').addEventListener('input', () => {
  state.linkRequest++;
  $('link-submit').disabled = true;
  clearTimeout(linkSearchTimer);
  linkSearchTimer = setTimeout(() => linkCandidates().catch(error => {
    $('link-error').textContent = error.message; $('link-error').hidden = false;
  }), 200);
});
$('link-form').addEventListener('submit', event => { event.preventDefault(); safe(submitLink)(); });
$('search').addEventListener('input', () => {
  state.search = $('search').value;
  ++state.chatRequest;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(safe(() => loadChats()), 200);
});
$('more-chats').addEventListener('click', safe(async () => {
  $('more-chats').disabled = true;
  try { await loadChats(true); } finally { $('more-chats').disabled = false; }
}));
$('sync').addEventListener('click', safe(sync));
$('match-contacts').addEventListener('click', safe(() => state.busy ? undefined : runJob('contacts', {})));
$('load').addEventListener('click', safe(sync));
$('older').addEventListener('click', safe(() => loadMessages(true)));
$('messages-tab').addEventListener('click', () => showTab('messages'));
$('analysis-tab').addEventListener('click', () => showTab('analysis'));
$('discover').addEventListener('click', () => showTab('analysis'));
// Prevent form navigation synchronously, before the async error wrapper yields.
$('analysis-form').addEventListener('submit', event => { event.preventDefault(); safe(runAnalysis)(event); });
$('analysis-form').addEventListener('change', () => {
  state.result = null;
  state.renderVersion++;
  if (state.chat) state.results.delete(state.chat.id);
  $('results').hidden = true; $('analysis-empty').hidden = false;
});
$('download').addEventListener('click', download);
$('back').addEventListener('click', backToWork);
function invalidateActivity() {
  state.activity = null;
  $('activity-results').hidden = true;
  $('activity-empty').hidden = false;
  $('activity-empty').textContent = 'Choose your filters, then select Plot to update the counts.';
}
function openPeopleActivity() {
  clearSelection();
  $('welcome').hidden = true;
  $('people-activity').hidden = false;
  document.querySelector('.app-shell').classList.add('has-chat');
}
async function plotPeopleActivity() {
  if (state.busy) return;
  const payload = {start_date: $('activity-start').value, end_date: $('activity-end').value,
    group_by: $('activity-group').value, metric: $('activity-metric').value,
    top: Number($('activity-top').value), ranking_mode: $('activity-ranking-mode').value, timezone: state.timezone};
  if (payload.start_date && payload.end_date && payload.start_date >= payload.end_date) throw new Error('Choose an end date after the start date. The end date is exclusive.');
  await runJob('activity', payload);
}
function activityName(person) {
  const duplicates = state.activityNames.get(person.name);
  return duplicates > 1 ? person.name + ' · ' + (person.addresses[0] || person.id.slice(-6)) : person.name;
}
const peopleColors = Array.from({length:15}, (_, index) => 'var(--series-' + (index + 1) + ')');
function activityColor(person, index) {
  if (state.activity.ranking_mode !== 'period') return peopleColors[index];
  let hash = 0;
  for (const char of person.id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) | 0;
  return `hsl(${(hash >>> 0) % 360} var(--series-saturation) 43%)`;
}
function activityPoints(person) {
  return person.points || person.values.map((count, index) => ({index, count}));
}
function activityPeriodRows(index) {
  const data = state.activity;
  if (data.ranking_mode === 'period') return data.period_leaders[index].map(row => ({...state.activityPeople.get(row.id), value: row.count}));
  return data.series.map(person => ({...person, value: person.values[index]})).sort((a, b) => b.value - a.value);
}
function renderPeopleActivity(data) {
  state.activity = data;
  state.activityPeople = new Map(data.series.map(person => [person.id, person]));
  state.activityNames = new Map();
  for (const person of data.series) state.activityNames.set(person.name, (state.activityNames.get(person.name) || 0) + 1);
  state.activityHidden = new Set();
  state.activityIndex = Math.max(0, data.periods.length - 1);
  $('activity-start').value = data.start_date || '';
  $('activity-end').value = data.end_date || '';
  $('activity-group').value = data.group_by;
  $('activity-metric').value = data.metric;
  $('activity-top').value = String(data.top);
  $('activity-ranking-mode').value = data.ranking_mode || 'overall';
  $('activity-chart-note').textContent = (data.ranking_mode === 'period'
    ? 'The top ' + data.top + ' are selected separately each ' + data.group_by + '. Dots show counts; lines connect consecutive qualifying periods. Gaps mean outside the top group or no messages. The legend follows the selected period. Ties use name, then identity.'
    : 'People are ranked across the selected date range.') + ' Select a name to show or hide its line. Move over the chart or use the period slider for counts.';
  $('activity-timezone').textContent = 'Direct conversations · ' + data.timezone;
  $('activity-results').hidden = false;
  $('activity-empty').hidden = true;
  const metricName = {total: 'sent + received', sent: 'sent', received: 'received'}[data.metric];
  $('activity-context').textContent = (data.start_date || 'All history') + ' → ' + (data.end_date ? data.end_date + ' (exclusive)' : 'latest') + (data.cached ? ' · cached' : '');
  $('activity-count').textContent = number(data.total) + ' messages ' + metricName;
  $('activity-period').max = String(Math.max(0, data.periods.length - 1));
  $('activity-period').disabled = data.periods.length < 2;
  const notes = [data.definition, data.ranking_mode === 'period' ? 'Periods without messages have no leaders.' : 'Periods without messages are shown as zero.'];
  if (data.group_by === 'week') notes.push('Weeks start Monday.');
  if (data.start_date || data.end_date) notes.push('The first and last periods may be partial.');
  if (data.invalid_dates) notes.push(number(data.invalid_dates) + ' messages without valid dates excluded.');
  if (data.unknown_direction) notes.push(number(data.unknown_direction) + ' messages with unknown direction excluded.');
  $('activity-definition').textContent = notes.join(' ');
  drawPeopleChart();
  const ranking = $('activity-ranking'); ranking.replaceChildren();
  $('ranking-context').textContent = 'Top ' + Math.min(25, data.ranking.length) + ' of ' + number(data.people_count) + ' people · ranked by ' + metricName + ' in this range';
  if (!data.ranking.length) {
    ranking.append(node('p', 'card-note', 'No matching direct messages.'));
    return;
  }
  const table = node('table'); const head = node('thead'); const header = node('tr');
  for (const label of ['Person', 'Sent', 'Received', 'Selected count', 'Share']) {
    const cell = node('th', '', label); cell.scope = 'col'; header.append(cell);
  }
  head.append(header); table.append(head); const body = node('tbody');
  for (const person of data.ranking.slice(0, 25)) {
    const row = node('tr'); const name = node('th'); name.scope = 'row';
    const button = node('button', 'text-button', person.name); button.title = person.addresses.join(', ');
    button.addEventListener('click', safe(async () => {
      await navigateWork({...baseQuery('direct'),target:person.id,group_by:data.group_by,start_date:data.start_date || '',end_date:data.end_date || ''});
    }));
    name.append(button); row.append(name);
    for (const value of [number(person.sent), number(person.received), number(person.count), (100 * person.count / data.total).toFixed(1) + '%']) row.append(node('td', '', value));
    body.append(row);
  }
  table.append(body); ranking.append(table);
}
function drawActivityLegend() {
  const data = state.activity;
  const legend = $('people-legend'); legend.replaceChildren();
  const people = data.ranking_mode === 'period' ? (data.periods.length ? activityPeriodRows(state.activityIndex) : []) : data.series;
  people.forEach((person, index) => {
    const button = node('button', 'person-legend' + (state.activityHidden.has(person.id) ? ' muted' : ''));
    const swatch = node('i'); swatch.style.background = activityColor(person, index);
    button.append(swatch, document.createTextNode(activityName(person)));
    button.title = person.addresses.join(', ');
    button.setAttribute('aria-pressed', String(!state.activityHidden.has(person.id)));
    button.addEventListener('click', () => {
      if (state.activityHidden.has(person.id)) state.activityHidden.delete(person.id); else state.activityHidden.add(person.id);
      drawPeopleChart();
    });
    legend.append(button);
  });
}
function drawPeopleChart() {
  const data = state.activity;
  if (!data) return;
  drawActivityLegend();
  const target = $('people-chart'); target.replaceChildren();
  state.activityCursor = null;
  if (!data.periods.length) {
    target.append(node('p', 'empty-list', 'No matching direct messages in this date range.'));
    $('activity-readout').replaceChildren();
    return;
  }
  const width = 1000, height = 320, left = 55, right = 20, top = 15, bottom = 35;
  const visible = data.series.filter(person => !state.activityHidden.has(person.id));
  let maximum = 1;
  for (const person of visible) for (const point of activityPoints(person)) maximum = Math.max(maximum, point.count);
  const step = Math.max(1, Math.ceil(maximum / 4)); maximum = step * 4;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = index => left + (data.periods.length === 1 ? plotWidth / 2 : index / (data.periods.length - 1) * plotWidth);
  const y = value => top + plotHeight * (1 - value / maximum);
  const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Message counts by person over time. Exact counts are available using the period slider.'});
  for (let i = 0; i <= 4; i++) {
    const value = step * i;
    svg.append(svgNode('line', {x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: 'var(--chart-grid)'}));
    svg.append(svgNode('text', {x: left - 10, y: y(value) + 4, 'text-anchor': 'end', fill: 'var(--chart-axis)', 'font-size': 11}, number(value)));
  }
  const tickStep = Math.max(1, Math.ceil(data.periods.length / 7));
  data.periods.forEach((period, index) => {
    if (index % tickStep !== 0 && index !== data.periods.length - 1) return;
    // Avoid crowding the last tick when it is close to the preceding label.
    if (index === data.periods.length - 1 && index % tickStep !== 0 && index % tickStep < tickStep / 2) return;
    svg.append(svgNode('text', {x: x(index), y: height - 10, 'text-anchor': 'middle', fill: 'var(--chart-axis)', 'font-size': 10}, data.group_by === 'month' ? period.slice(0, 7) : period));
  });
  data.series.forEach((person, index) => {
    if (state.activityHidden.has(person.id)) return;
    const points = activityPoints(person), color = activityColor(person, index);
    const path = points.map((point, i) => (i && points[i - 1].index === point.index - 1 ? 'L' : 'M') + x(point.index).toFixed(2) + ',' + y(point.count).toFixed(2)).join(' ');
    const line = svgNode('path', {d: path, stroke: color, 'stroke-width': 2.5, fill: 'none', 'stroke-linejoin': 'round'});
    line.append(svgNode('title', {}, activityName(person))); svg.append(line);
    if (data.ranking_mode === 'period' || data.periods.length === 1) for (const point of points) {
      const dot = svgNode('circle', {cx: x(point.index), cy: y(point.count), r: 3, fill: color});
      dot.append(svgNode('title', {}, activityName(person) + ' · ' + data.periods[point.index] + ': ' + number(point.count)));
      svg.append(dot);
    }
  });
  const cursor = svgNode('line', {x1: x(state.activityIndex), x2: x(state.activityIndex), y1: top, y2: height - bottom, stroke: 'var(--chart-cursor)', 'stroke-dasharray': '4 4', 'pointer-events': 'none'});
  svg.append(cursor); target.append(svg);
  state.activityCursor = {element: cursor, x};
  svg.addEventListener('pointermove', event => {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const index = Math.round((point.x - left) / plotWidth * (data.periods.length - 1));
    setActivityPeriod(index);
  });
  setActivityPeriod(state.activityIndex, true);
}
function setActivityPeriod(index, force = false) {
  const data = state.activity;
  if (!data?.periods.length) return;
  index = Math.max(0, Math.min(data.periods.length - 1, index));
  if (!force && index === state.activityIndex) return;
  state.activityIndex = index; $('activity-period').value = String(index);
  if (state.activityCursor) {
    const x = state.activityCursor.x(index);
    state.activityCursor.element.setAttribute('x1', x); state.activityCursor.element.setAttribute('x2', x);
  }
  const period = data.periods[index];
  $('activity-period').setAttribute('aria-valuetext', period);
  const readout = $('activity-readout');
  readout.replaceChildren(node('strong', '', (data.group_by === 'month' ? period.slice(0, 7) : period) + (data.group_by === 'week' ? ' · week starting' : '')));
  if (data.ranking_mode === 'period') drawActivityLegend();
  const rows = activityPeriodRows(index);
  if (!rows.length) readout.append(node('span', '', 'No messages this period.'));
  rows.forEach((person, rank) => {
    const hidden = state.activityHidden.has(person.id);
    if (hidden && data.ranking_mode !== 'period') return;
    readout.append(node('span', '', (data.ranking_mode === 'period' ? (rank + 1) + '. ' : '') + activityName(person) + ': ' + number(person.value) + (hidden ? ' (hidden)' : '')));
  });
  readout.append(node('span', 'period-total', 'All people: ' + number(data.period_totals[index])));
}
function downloadActivity() {
  if (!state.activity) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.activity, null, 2) + '\n'], {type: 'application/json'}));
  const link = node('a'); link.href = url; link.download = 'pymessage-people-activity.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('open-activity').addEventListener('click', safe(() => navigateWork(work.route.query, 'activity')));
$('activity-back').addEventListener('click', backToWork);
$('activity-form').addEventListener('submit', event => { event.preventDefault(); safe(plotPeopleActivity)(); });
$('activity-form').addEventListener('change', invalidateActivity);
$('activity-period').addEventListener('input', () => setActivityPeriod(Number($('activity-period').value)));
$('activity-download').addEventListener('click', downloadActivity);

async function initialize() {
  const status = await json('/api/status');
  state.timezone = status.timezone;
  state.demo = status.demo;
  contactStatus(status.contacts);
  $('match-contacts').disabled = state.demo;
  state.contactLabels = await json('/api/contact-labels');
  $('timezone-label').textContent = status.timezone;
  $('activity-timezone').textContent = 'Direct conversations · ' + status.timezone;
  $('mode-badge').textContent = status.demo ? 'DEMO' : 'LOCAL';
  if (status.demo) {
    $('load').textContent = 'Load demo conversations';
    notify('Demo mode · fictional conversations in a separate archive.');
  }
  await initializeWork(status);
  // Startup displays the saved list only. Messages and analyses load on demand.
  if (status.job.state === 'running') {
    setBusy(true, status.job.kind);
    notify(status.job.kind === 'activity' ? 'Counting messages across people…' : status.job.kind === 'contacts' ? 'Reading Contacts… Check for a macOS permission prompt.' : status.job.kind === 'sync' ? 'Loading your conversations…' : 'Analysis is running…');
    try { await watchJob(status.job.id); } finally { setBusy(false); }
  } else if (status.job.state === 'complete' && status.job.kind === 'explore') {
    acceptWorkResult(status.job.result);
  } else if (status.job.state === 'failed') {
    notify(status.job.error, true);
  } else if (status.job.state === 'complete' && status.job.kind === 'word-context') {
    renderWordContext(status.job.result);
  } else if (status.job.state === 'complete' && status.job.kind === 'semantic') {
    renderSemantic(status.job.result);
  } else if (status.job.state === 'complete' && status.job.kind === 'activity') {
    renderPeopleActivity(status.job.result);
  } else if (status.job.state === 'complete' && status.job.kind === 'analyze') {
    state.results.set(status.job.result.chat_id, status.job.result);
    if (state.chat?.id === status.job.result.chat_id) {
      restoreFilters(status.job.result.config);
      await renderResults(status.job.result);
    }
  }
}
bindWork();
initializeSemanticUI();
initializeContextUI();
safe(initialize)();
