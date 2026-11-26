'use strict';

function invalidateSemantic() {
  state.semantic = null;
  $('semantic-results').hidden = true;
  $('semantic-empty').hidden = false;
  $('semantic-empty').textContent = 'Select Analyze vocabulary to update these results.';
}
function openSemantic() {
  clearSelection();
  $('welcome').hidden = true;
  $('semantic-view').hidden = false;
  document.querySelector('.app-shell').classList.add('has-chat');
}
function semanticPersonLabel(person) {
  return person.name + (person.addresses?.length ? ' · ' + person.addresses[0] : '');
}
function renderSemanticSelection(prefix = 'semantic') {
  const selected = state[prefix + 'Selected'];
  const target = $(prefix + '-selected'); target.replaceChildren();
  $(prefix + '-selection-count').textContent = selected.size ? '(' + selected.size + ' selected)' : '(everyone)';
  for (const person of selected.values()) {
    const button = node('button', 'text-button', semanticPersonLabel(person) + ' ×');
    button.type = 'button'; button.disabled = state.busy;
    button.setAttribute('aria-label', 'Remove ' + semanticPersonLabel(person));
    button.addEventListener('click', () => {
      if (state.busy) return;
      selected.delete(person.id); renderSemanticSelection(prefix); invalidatePersonAnalysis(prefix);
    });
    target.append(button);
  }
}
async function findSemanticPeople(prefix = 'semantic') {
  const selected = state[prefix + 'Selected'];
  const request = ++state[prefix + 'Search'];
  const query = $(prefix + '-search').value.trim();
  if (!query) { $(prefix + '-matches').replaceChildren(); return; }
  const result = await json('/api/people?' + new URLSearchParams({q: query}));
  if (request !== state[prefix + 'Search']) return;
  const target = $(prefix + '-matches'); target.replaceChildren();
  for (const person of result.people.slice(0, 15)) {
    const button = node('button', 'text-button', semanticPersonLabel(person));
    button.type = 'button'; button.disabled = state.busy;
    button.addEventListener('click', () => {
      if (state.busy) return;
      const current = state[prefix + 'Selected'];
      if (current.size >= 6 && !current.has(person.id)) { notify('Select up to six people.', true); return; }
      current.set(person.id, person); renderSemanticSelection(prefix); invalidatePersonAnalysis(prefix);
    });
    target.append(button);
  }
  if (!result.people.length) target.append(node('p', 'card-note', 'No matching people.'));
  if (result.people.length > 15 || result.has_more) target.append(node('p', 'card-note', 'Showing the first 15 matches. Refine your search for more.'));
}
async function runSemantic() {
  if (state.busy) return;
  const payload = {start_date: $('semantic-start').value, end_date: $('semantic-end').value,
    group_by: $('semantic-group').value, timezone: state.timezone,
    names: $('semantic-names').value.split(',').map(s => s.trim()).filter(Boolean),
    person_ids: [...state.semanticSelected.keys()]};
  if (payload.start_date && payload.end_date && payload.start_date >= payload.end_date) throw new Error('Choose an end date after the start date.');
  await runJob('semantic', payload);
}
function semanticTable(target, headings, rows) {
  target.replaceChildren();
  if (!rows.length) { target.append(node('p', 'card-note', 'Not enough matching text for this comparison.')); return; }
  const table = node('table'), head = node('thead'), tr = node('tr'), body = node('tbody');
  for (const label of headings) { const th = node('th', '', label); th.scope = 'col'; tr.append(th); }
  head.append(tr); table.append(head);
  for (const values of rows) {
    const row = node('tr');
    values.forEach((value, i) => { const cell = node(i ? 'td' : 'th', '', value); if (!i) cell.scope = 'row'; row.append(cell); });
    body.append(row);
  }
  table.append(body); target.append(table);
}
function semanticValue(value, digits = 1) { return value == null ? '—' : Number(value).toFixed(digits); }
function semanticWordButton(word, count) {
  const button = node('button', 'word-chip');
  button.append(node('span','',word), node('b','',number(count)));
  button.type = 'button'; button.disabled = state.busy;
  button.addEventListener('click', safe(async () => {
    if (state.busy) return;
    const selected = new Map(state.semanticSelected);
    const query = {...work.route.query, start_date:$('semantic-start').value, end_date:$('semantic-end').value,
      group_by:$('semantic-group').value};
    await navigateWork(query, 'context');
    state.contextSelected = selected; renderSemanticSelection('context');
    $('context-words').value = word; invalidateContext(); await runWordContext();
  }));
  return button;
}
function renderSemantic(data) {
  state.semantic = data;
  $('semantic-results').hidden = false; $('semantic-empty').hidden = true;
  $('semantic-start').value = data.start_date || ''; $('semantic-end').value = data.end_date || '';
  $('semantic-group').value = data.group_by;
  $('semantic-names').value = data.extra_names.join(', ');
  const old = state.semanticSelected;
  state.semanticSelected = new Map(data.person_ids.map(id => [id, data.people.find(p => p.id === id) || old.get(id) || {id, name: 'Selected person', addresses: []}]));
  renderSemanticSelection();
  $('semantic-meta').textContent = (data.start_date || 'All history') + ' → ' + (data.end_date ? data.end_date + ' (exclusive)' : 'latest') + ' · ' + data.timezone + (data.cached ? ' · cached' : '');
  const s = data.summary;
  $('semantic-stats').replaceChildren(metric('Sent text messages', number(s.messages)), metric('Words', number(s.words)), metric('Unique words', number(s.unique_words)), metric('Unique words per 100', semanticValue(s.diversity), number(s.diversity_windows) + ' complete samples'));
  semanticTable($('semantic-people'), ['Person', 'Text messages', 'Words / message', 'Unique / 100', 'Distinctive words', 'Frequent words'], data.people.map(p => [semanticPersonLabel(p), number(p.messages), semanticValue(p.words_per_message), semanticValue(p.diversity), p.distinctive.map(w => w.word + ' (+' + semanticValue(w.delta) + '/1,000)').join(', ') || '—', p.top_words.map(w => w.word + ' (' + w.count + ')').join(', ')]));
  $('semantic-words').replaceChildren(...data.top_words.map(w => semanticWordButton(w.word, w.count)));
  if (!data.top_words.length) $('semantic-words').append(node('p', 'card-note', 'No words to display.'));
  const tone = $('semantic-tone'), previousTone = tone.value; tone.replaceChildren();
  for (const label of Object.keys(s.tone)) { const option = node('option', '', label); option.value = label; tone.append(option); }
  if (Object.hasOwn(s.tone, previousTone)) tone.value = previousTone;
  semanticTable($('semantic-tone-table'), ['Person', ...Object.keys(s.tone).map(label => label + ' %')], [{name: 'Everyone selected', ...s}, ...data.people].map(p => [p.addresses ? semanticPersonLabel(p) : p.name, ...Object.keys(s.tone).map(label => semanticValue(p.tone[label].rate))]));
  $('semantic-tone-method').replaceChildren(...Object.entries(data.tone_lexicon).map(([label, list]) => node('p', '', label + ': ' + list.join(', '))), node('p', '', 'Laughter also includes 😂 and 🤣. Questions match ? or ？. Negations: not, no, never, don’t/dont, didn’t/didnt, isn’t/isnt, wasn’t/wasnt, can’t/cant.'));
  const nameSelect = $('semantic-name'), previousName = nameSelect.value; nameSelect.replaceChildren();
  for (const name of data.names) { const option = node('option', '', name.name); option.value = name.name; nameSelect.append(option); }
  if (data.names.some(n => n.name === previousName)) nameSelect.value = previousName;
  nameSelect.disabled = !data.names.length;
  semanticTable($('semantic-name-table'), ['Name text', 'Messages', '% of text messages', 'Match details'], data.names.map(n => [n.name, number(n.count), semanticValue(n.rate), [n.custom ? 'Entered name' : 'Contact name', n.first_name_only ? 'First-name match' : '', n.ambiguous ? 'Multiple contacts share this name' : '', n.contacts.join(', ')].filter(Boolean).join(' · ')]));
  const byPerson = node('div', 'reaction-table');
  semanticTable(byPerson, ['Person', 'Top name matches (messages)'], data.people.filter(p => p.names.length).map(p => [semanticPersonLabel(p), p.names.map(n => n.name + ': ' + n.count).join(', ')]));
  if (data.people.some(p => p.names.length)) $('semantic-name-table').append(byPerson);
  const excluded = Object.entries(data.coverage).map(([key, count]) => key.replaceAll('_', ' ') + ': ' + number(count)).join(' · ');
  $('semantic-coverage').textContent = 'Sent text only; group conversations, incoming messages, reactions, and duplicate GUIDs are excluded. Edited text uses its current archived form. Weeks start Monday; boundary periods can be partial. Showing up to 30 people and the top 30 name matches, plus every name you entered. ' + (excluded ? 'Other exclusions — ' + excluded + '.' : '');
  drawSemanticCharts();
}

const trendLayouts = new WeakMap();
const resizeTrends = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
  for (const {target} of entries) {
    if (!target.isConnected) {
      resizeTrends.unobserve(target);
      trendLayouts.delete(target);
      continue;
    }
    const layout = trendLayouts.get(target);
    if (layout && target.clientWidth && target.clientWidth !== layout.width) layout.redraw();
  }
});

function semanticChart(target, periods, series, unit, initialPeriod = periods.length - 1) {
  resizeTrends?.unobserve(target);
  trendLayouts.delete(target);
  target.replaceChildren(); target.classList.add('people-chart');
  if (!periods.length || !series.some(s => s.values.some(v => v != null))) { target.append(node('p', 'card-note', 'No data for this measure.')); return; }
  const tickLabel = new Intl.NumberFormat(undefined, {maximumSignificantDigits:3});
  const width = target.clientWidth || 900, height = width < 500 ? 190 : 235;
  const left = 48, right = 34, top = 12, bottom = 30;
  const x = i => left + (periods.length === 1 ? (width - left - right) / 2 : i / (periods.length - 1) * (width - left - right));
  let max = 1; for (const s of series) for (const value of s.values) if (value != null) max = Math.max(max, value);
  const y = value => top + (height - top - bottom) * (1 - value / max);
  const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': unit + ' by period. Use the slider for exact values.'});
  for (let i = 0; i <= 4; i++) {
    const value = max * i / 4;
    svg.append(svgNode('line', {x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: 'var(--chart-grid)'}), svgNode('text', {x: left - 8, y: y(value) + 4, 'text-anchor': 'end', fill: 'var(--chart-axis)', 'font-size': 11}, tickLabel.format(value)));
  }
  const legend = node('div', 'people-legend');
  series.forEach((s, index) => {
    const color = peopleColors[index % peopleColors.length];
    const label = node('span', 'person-legend'), swatch = node('i'); swatch.style.background = color; label.append(swatch, document.createTextNode(s.name)); legend.append(label);
    let previous = -2;
    const path = [];
    s.values.forEach((value, i) => {
      if (value == null) return;
      path.push((previous === i - 1 ? 'L' : 'M') + x(i).toFixed(2) + ',' + y(value).toFixed(2)); previous = i;
      if (s.values[i - 1] == null && s.values[i + 1] == null) svg.append(svgNode('circle', {cx: x(i), cy: y(value), r: 3, fill: color}));
    });
    const line = svgNode('path', {d: path.join(' '), fill: 'none', stroke: color, 'stroke-width': 2.5}); line.append(svgNode('title', {}, s.name)); svg.append(line);
  });
  const tickCount = Math.max(2, Math.min(8, Math.floor((width - left - right) / 90)));
  const step = Math.max(1, Math.ceil(periods.length / tickCount));
  periods.forEach((period, i) => { if (i % step === 0) svg.append(svgNode('text', {x: x(i), y: height - 8, 'text-anchor': 'middle', fill: 'var(--chart-axis)', 'font-size': 11}, period)); });
  const cursor = svgNode('line', {y1: top, y2: height - bottom, stroke: 'var(--chart-cursor)', 'stroke-dasharray': '4 4'}); svg.append(cursor);
  const label = node('label', 'period-selector', 'Period'), slider = node('input'), readout = node('div', 'activity-readout');
  slider.type = 'range'; slider.min = '0'; slider.max = String(periods.length - 1); slider.disabled = periods.length < 2; slider.setAttribute('aria-label', unit + ' period'); label.append(slider); readout.setAttribute('aria-live', 'polite');
  let selected = -1;
  const show = index => {
    index = Math.max(0, Math.min(periods.length - 1, index)); if (selected === index) return; selected = index;
    slider.value = String(index); slider.setAttribute('aria-valuetext', periods[index]); cursor.setAttribute('x1', x(index)); cursor.setAttribute('x2', x(index));
    readout.replaceChildren(node('strong', '', periods[index]), ...series.map(s => node('span', '', s.name + ': ' + semanticValue(s.values[index], 2) + ' ' + unit)));
  };
  slider.addEventListener('input', () => show(Number(slider.value)));
  svg.addEventListener('pointermove', event => {
    const matrix = svg.getScreenCTM(); if (!matrix) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    show(Math.round((point.x - left) / (width - left - right) * (periods.length - 1)));
  });
  target.append(legend, svg, label, readout); show(initialPeriod);
  if (resizeTrends) {
    trendLayouts.set(target, {width, redraw: () => semanticChart(target, periods, series, unit, selected)});
    resizeTrends.observe(target);
  }
}
function drawSemanticCharts() {
  const data = state.semantic; if (!data) return;
  const periods = data.timeline.map(r => r.period);
  const people = data.person_ids.length ? data.people.map(p => ({name: semanticPersonLabel(p), timeline: p.timeline})) : [{name: 'Everyone', timeline: data.timeline}];
  const measure = $('semantic-measure').value, tone = $('semantic-tone').value;
  semanticChart($('semantic-trend'), periods, people.map(p => ({name: p.name, values: p.timeline.map(r => r[measure])})), {diversity: 'unique / 100 words', words: 'words', words_per_message: 'words / message'}[measure]);
  semanticChart($('semantic-tone-chart'), periods, people.map(p => ({name: p.name, values: p.timeline.map(r => r.tone[tone]?.rate ?? null)})), '% of messages with ' + tone);
  const name = data.names.find(n => n.name === $('semantic-name').value);
  semanticChart($('semantic-name-chart'), periods, name ? [{name: name.name, values: name.values.map((count, i) => data.timeline[i].messages ? 100 * count / data.timeline[i].messages : null)}] : [], '% of messages mentioning name');

}
function initializeSemanticUI() {
  $('open-semantic').addEventListener('click', safe(() => navigateWork(work.route.query, 'semantic')));
  $('semantic-back').addEventListener('click', () => backToWork());
  $('semantic-form').addEventListener('submit', event => { event.preventDefault(); safe(runSemantic)(); });
  $('semantic-form').addEventListener('change', event => { if (event.target.id !== 'semantic-search') invalidateSemantic(); });
  let timer;
  $('semantic-search').addEventListener('input', () => { clearTimeout(timer); state.semanticSearch++; timer = setTimeout(safe(findSemanticPeople), 250); });
  for (const id of ['semantic-measure', 'semantic-tone', 'semantic-name']) $(id).addEventListener('change', drawSemanticCharts);
  $('semantic-download').addEventListener('click', () => {
    if (!state.semantic) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.semantic, null, 2) + '\n'], {type: 'application/json'}));
    const link = node('a'); link.href = url; link.download = 'pymessage-vocabulary.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

function invalidatePersonAnalysis(prefix) {
  if (prefix === 'context') invalidateContext(); else invalidateSemantic();
}
function invalidateContext() {
  state.wordContext = null;
  $('context-results').hidden = true;
  $('context-empty').hidden = false;
  $('context-empty').textContent = 'Select Analyze words to update these results.';
}
function openWordContext() {
  clearSelection();
  $('welcome').hidden = true;
  $('context-view').hidden = false;
  document.querySelector('.app-shell').classList.add('has-chat');
}
async function runWordContext() {
  if (state.busy) return;
  const words = contextSelections();
  if (!words.length || words.length > 6) throw new Error('Enter one to six words or emoji, separated by commas or spaces.');
  const payload = {words, person_ids: [...state.contextSelected.keys()], timezone: state.timezone,
    start_date: $('context-start').value, end_date: $('context-end').value, group_by: $('context-group').value};
  if (payload.start_date && payload.end_date && payload.start_date >= payload.end_date) throw new Error('Choose an end date after the start date.');
  await runJob('word-context', payload);
}
function renderWordContext(data) {
  state.wordContext = data;
  $('context-results').hidden = false; $('context-empty').hidden = true;
  $('context-words').value = data.words.join(', ');
  $('context-start').value = data.start_date || ''; $('context-end').value = data.end_date || '';
  $('context-group').value = data.group_by;
  const old = state.contextSelected;
  state.contextSelected = new Map(data.person_ids.map(id => [id, data.contexts.flatMap(c => c.people).find(p => p.id === id) || old.get(id) || {id, name: 'Selected person', addresses: []}]));
  renderSemanticSelection('context');
  $('context-meta').textContent = (data.start_date || 'All history') + ' → ' + (data.end_date ? data.end_date + ' (exclusive)' : 'latest') + ' · ' + data.timezone + (data.cached ? ' · cached' : '');
  const periods = data.contexts[0].timeline.map(r => r.period);
  semanticChart($('context-comparison-chart'), periods, data.contexts.map(c => ({name: c.word, values: c.timeline.map(r => r.rate)})), 'uses / 1,000 tokens');
  const tabs = $('context-tabs'); tabs.replaceChildren();
  if (!data.words.includes(state.contextWord)) state.contextWord = data.words[0];
  for (const word of data.words) {
    const button = node('button', 'text-button', word); button.type = 'button';
    button.addEventListener('click', () => { state.contextWord = word; renderContextDetails(); });
    tabs.append(button);
  }
  const coverage = data.contexts.find(c => c.coverage)?.coverage || {};
  $('context-coverage').textContent = Object.entries(coverage).map(([key, count]) => key.replaceAll('_', ' ') + ' excluded: ' + number(count)).join(' · ');
  renderContextDetails();
}
function renderContextDetails() {
  const data = state.wordContext; if (!data) return;
  const context = data.contexts.find(c => c.word === state.contextWord) || data.contexts[0];
  const total = context.timeline.reduce((sum, row) => sum + row.count, 0);
  for (const button of $('context-tabs').children) {
    const active = button.textContent === context.word;
    button.setAttribute('aria-pressed', String(active)); button.classList.toggle('active', active);
  }
  $('context-detail-heading').textContent = context.word;
  $('context-detail-note').textContent = number(total) + ' uses in this selection. ' + (data.person_ids.length ? 'The lines below compare your usage with each selected person.' : 'Filter by person to compare individual timelines.');
  const periods = context.timeline.map(r => r.period);
  $('context-person-chart').hidden = !data.person_ids.length;
  if (data.person_ids.length) semanticChart($('context-person-chart'), periods, context.people.map(p => ({name: semanticPersonLabel(p), values: p.timeline.map(r => r.rate)})), 'uses / 1,000 tokens');
  semanticTable($('context-periods'), ['Period', 'Uses', 'Uses / 1,000', 'Context change', 'Nearby words'], context.timeline.filter(r => r.count).map(r => [r.period, number(r.count), semanticValue(r.rate), semanticValue(r.context_change, 2), r.neighbors.map(w => w.word + ' (' + w.count + ')').join(', ') || '—']));
  semanticTable($('context-people'), ['Sent to', 'Uses', 'Uses / 1,000', 'Nearby words'], context.people.map(p => [semanticPersonLabel(p), number(p.count), semanticValue(p.rate), p.neighbors.map(w => w.word + ' (' + w.count + ')').join(', ') || '—']));
}
function initializeContextUI() {
  $('context-emoji-button').addEventListener('click', openContextEmojiPicker);
  $('context-emoji-close').addEventListener('click', () => $('context-emoji-dialog').close());
  $('context-emoji-dialog').addEventListener('close', () => $('context-emoji-button').focus());
  for (const [id, event] of [['context-emoji-search', 'input'], ['context-emoji-group', 'change']]) {
    $(id).addEventListener(event, () => { state.contextEmojiLimit = 72; renderContextEmojiPicker(); });
  }
  $('context-emoji-more').addEventListener('click', () => { state.contextEmojiLimit += 72; renderContextEmojiPicker(); });
  $('open-context').addEventListener('click', safe(() => navigateWork(work.route.query, 'context')));
  $('context-back').addEventListener('click', () => backToWork());
  $('context-form').addEventListener('submit', event => { event.preventDefault(); safe(runWordContext)(); });
  $('context-form').addEventListener('change', event => { if (event.target.id !== 'context-search') invalidateContext(); });
  let timer;
  $('context-search').addEventListener('input', () => { clearTimeout(timer); state.contextSearch++; timer = setTimeout(safe(() => findSemanticPeople('context')), 250); });
  $('context-download').addEventListener('click', () => {
    if (!state.wordContext) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.wordContext, null, 2) + '\n'], {type: 'application/json'}));
    const link = node('a'); link.href = url; link.download = 'pymessage-word-context.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

function contextSelectionKey(value) {
  return value.normalize('NFC').toLowerCase().replace(/[\uFE0E\uFE0F]/g, '').replace(/’/g, "'");
}
function contextSelections() {
  const seen = new Set();
  return $('context-words').value.trim().split(/[,\s]+/).filter(value => {
    if (!value) return false;
    const key = contextSelectionKey(value);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
async function openContextEmojiPicker() {
  if (state.busy) return;
  const dialog = $('context-emoji-dialog');
  if (!dialog.open) dialog.showModal();
  $('context-emoji-search').focus();
  state.contextEmojiLimit = 72;
  if (state.contextEmojiData) { renderContextEmojiPicker(); return; }
  $('context-emoji-status').textContent = 'Loading emoji…';
  try {
    // This is a bundled public catalog. No Messages or Contacts query is made.
    state.contextEmojiLoading ||= json('/emoji-picker.json').then(data => {
      state.contextEmojiData = data.emoji;
      const groups = $('context-emoji-group'); groups.replaceChildren();
      const all = node('option', '', 'All categories'); all.value = ''; groups.append(all);
      for (const group of new Set(data.emoji.map(item => item.group))) {
        const option = node('option', '', group); option.value = group; groups.append(option);
      }
    }).finally(() => { state.contextEmojiLoading = null; });
    await state.contextEmojiLoading;
    if (dialog.open) renderContextEmojiPicker();
  } catch (error) {
    $('context-emoji-status').textContent = 'Could not load the emoji picker. Close and reopen it to retry. ' + error.message;
  }
}
function renderContextEmojiPicker(focusEmoji = '') {
  const selected = contextSelections(), keys = new Set(selected.map(contextSelectionKey));
  const query = contextSelectionKey($('context-emoji-search').value.trim()).split(/\s+/).filter(Boolean);
  const group = $('context-emoji-group').value;
  const aliases = {'😂': 'laugh laughing lol', '🤣': 'laugh laughing lol', '❤️': 'love', '👍': 'like yes okay', '👎': 'dislike no', '🙏': 'please thanks thank pray', '😭': 'cry crying sad'};
  const matches = (state.contextEmojiData || []).filter(item => {
    const text = contextSelectionKey(item.emoji + ' ' + item.name + ' ' + item.group + ' ' + (aliases[item.emoji] || ''));
    return (!group || item.group === group) && query.every(term => text.includes(term));
  });
  const limit = state.contextEmojiLimit || 72, grid = $('context-emoji-grid'); grid.replaceChildren();
  $('context-emoji-selection').textContent = selected.length + '/6 selected' + (selected.length ? ' · ' + selected.join(', ') : '');
  $('context-emoji-status').textContent = matches.length ? 'Showing ' + Math.min(limit, matches.length) + ' of ' + matches.length + ' emoji' : 'No emoji match. Try another search or category.';
  $('context-emoji-more').hidden = matches.length <= limit;
  let focusButton;
  for (const item of matches.slice(0, limit)) {
    const key = contextSelectionKey(item.emoji), chosen = keys.has(key);
    const button = node('button', 'emoji-choice' + (chosen ? ' selected' : ''), item.emoji);
    button.type = 'button'; button.title = item.name;
    button.setAttribute('aria-label', item.name); button.setAttribute('aria-pressed', String(chosen));
    button.disabled = state.busy || (!chosen && selected.length >= 6);
    button.addEventListener('click', () => {
      if (state.busy) return;
      const current = contextSelections();
      const exists = current.some(value => contextSelectionKey(value) === key);
      if (!exists && current.length >= 6) return;
      $('context-words').value = (exists ? current.filter(value => contextSelectionKey(value) !== key) : [...current, item.emoji]).join(', ');
      invalidateContext(); renderContextEmojiPicker(item.emoji);
    });
    grid.append(button);
    if (focusEmoji === item.emoji) focusButton = button;
  }
  if (focusButton && !focusButton.disabled) focusButton.focus();
}
