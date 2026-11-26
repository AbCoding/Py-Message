// Render fictional results with a small DOM fixture; no app or real data load.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag, text = '') {
    this.tag = tag; this.textContent = text; this.children = []; this.attributes = {}; this.style = {};
    this.value = ''; this.listeners = {}; this.classList = {add() {}, remove() {}, toggle() {}};
  }
  append(...children) {
    this.children.push(...children);
    if (this.children.length === children.length && children[0]?.tag === 'option') this.value = children[0].value;
  }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
const elements = new Map();
const $ = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
const state = {semantic: null, semanticSelected: new Map(), semanticSearch: 0, busy: false, timezone: 'UTC', wordContext: null, contextSelected: new Map(), contextSearch: 0, contextWord: ''};
const calls = [];
let catalogLoads = 0;
const context = vm.createContext({state, $, Set, Map, Object, Number,
  number: String, safe: fn => fn,
  peopleColors: ['blue', 'red'],
  document: {createTextNode: text => new Element('text', text), querySelector: () => new Element('div')},
  work: {route:{query:{}}},
  navigateWork: (q, panel) => panel === 'semantic' ? context.openSemantic() : context.openWordContext(),
  clearSelection() {},
  notify() {},
  json(path) {
    if (path !== '/emoji-picker.json') throw new Error('Picker must not read message data');
    catalogLoads++;
    return Promise.resolve(JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../pymessage/web/emoji-picker.json'), 'utf8')));
  },
  runJob: async (kind, payload) => calls.push({kind, payload}),
  node: (tag, css, text = '') => new Element(tag, text),
  metric: (label, value) => new Element('metric', label + ': ' + value),
  svgNode: (tag, attrs, text = '') => { const el = new Element(tag, text); el.attributes = attrs; return el; },
});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../pymessage/web/semantic.js'), 'utf8'), context);
context.initializeSemanticUI();
context.initializeContextUI();
$('open-semantic').listeners.click();
assert.equal($('semantic-view').hidden, false);
assert.equal(calls.length, 0);

const tone = {'laughter': {count: 1, rate: 50}};
const summary = {messages: 2, words: 120, unique_words: 35, diversity: 30, diversity_windows: 1, words_per_message: 60, tone};
const timeline = [{period: '2024-01-01', ...summary}, {period: '2024-02-01', ...summary, messages: 0, words: 0, words_per_message: null, diversity: null, tone: {laughter: {count: 0, rate: null}}}, {period: '2024-03-01', ...summary}];
const person = {id: 'a', name: '<script>Alex</script>', addresses: ['a@example.invalid'], ...summary, timeline, names: [], top_words: [{word: 'coffee', count: 6}], distinctive: []};
const data = {group_by: 'month', timezone: 'UTC', person_ids: ['a'], extra_names: [], summary, timeline,
  top_words: [{word: 'coffee', count: 6}],
  people: [person], people_count: 1, tone_lexicon: {laughter: ['lol']}, names: [], coverage: {}, context: null};
$('semantic-measure').value = 'diversity';
context.renderSemantic(data);
assert.equal($('semantic-results').hidden, false);
assert.equal(state.semanticSelected.size, 1);
assert.equal($('semantic-name').disabled, true);
const chart = $('semantic-trend').children.find(el => el.tag === 'svg');
const path = chart.children.find(el => el.tag === 'path').attributes.d;
assert.equal((path.match(/M/g) || []).length, 2);
assert.ok(!path.includes('L'), 'A low-sample period must leave a gap');
assert.equal(chart.children.filter(el => el.tag === 'circle').length, 2);
assert.ok($('semantic-selected').children[0].textContent.includes('<script>Alex</script>'), 'Names are passed as text');

const withContext = {...data, names: [{name: 'alex', count: 1, rate: 50, values: [1, 0, 0], contacts: ['Alex Smith', 'Alex Jones'], first_name_only: true, ambiguous: true, custom: false}],
  context: {word: 'coffee', people: [], timeline: timeline.map((r, i) => ({period: r.period, count: i ? 0 : 6, rate: i === 1 ? null : i ? 0 : 50, context_change: null, neighbors: i ? [] : [{word: 'coding', count: 3}]}))}};
context.renderSemantic(withContext);
assert.equal($('semantic-name').disabled, false);
const nameText = $('semantic-name-table').children[0].children[1].children[0].children[3].textContent;
assert.ok(nameText.includes('Multiple contacts'));


(async () => {
  await $('semantic-words').children[0].listeners.click();
  assert.equal(calls[0].kind, 'word-context');
  assert.equal(calls[0].payload.words[0], 'coffee');
  assert.equal(calls[0].payload.person_ids[0], 'a');
  assert.equal($('context-results').hidden, true);
  assert.equal($('semantic-results').hidden, false);
  assert.equal($('context-view').hidden, false);
  const coffee = {...withContext.context, people: [{...person, count: 6, rate: 50, neighbors: [{word: 'coding', count: 3}], timeline: withContext.context.timeline}]};
  const tea = {...coffee, word: 'tea', timeline: coffee.timeline.map(r => ({...r, count: 0, rate: r.rate == null ? null : 0, neighbors: []})), people: [{...coffee.people[0], count: 0, rate: 0, neighbors: []}]};
  context.renderWordContext({group_by: 'month', timezone: 'UTC', person_ids: ['a'], words: ['coffee', 'tea'], contexts: [coffee, tea], cached: false});
  assert.equal($('context-tabs').children.length, 2);
  assert.ok($('context-detail-note').textContent.includes('6 uses'));
  assert.ok($('context-periods').children[0].children[1].children[0].children[4].textContent.includes('coding'));
  assert.equal($('context-person-chart').hidden, false);
  const priorCalls = calls.length;
  $('context-tabs').children[1].listeners.click();
  assert.equal($('context-detail-heading').textContent, 'tea');
  assert.ok($('context-detail-note').textContent.includes('0 uses'));
  assert.equal(calls.length, priorCalls, 'Switching words must not run another analysis');
  $('context-words').value = 'coffee, tea coffee';
  await context.runWordContext();
  assert.equal(calls.at(-1).payload.words.length, 2);
  assert.equal(calls.at(-1).payload.person_ids[0], 'a');
  $('context-selected').children[0].listeners.click();
  assert.equal(state.contextSelected.size, 0);
  assert.equal(state.semanticSelected.size, 1, 'Person filters belong to their own view');
  assert.equal($('context-results').hidden, true);
  $('open-context').listeners.click();
  assert.equal(calls.length, priorCalls + 1, 'Opening Word context must not start a scan');
  $('context-words').value = '';
  await assert.rejects(context.runWordContext(), /one to six/);
  $('context-words').value = '😂, 👨‍👩‍👧‍👦, 👍🏽, love';
  await context.runWordContext();
  assert.equal(calls.at(-1).payload.words.join('|'), '😂|👨‍👩‍👧‍👦|👍🏽|love');
  const emoji = {...coffee, word: '👨‍👩‍👧‍👦', rate_unit: 'tokens', people: []};
  context.renderWordContext({group_by: 'month', timezone: 'UTC', person_ids: [], words: ['👨‍👩‍👧‍👦', 'love'], contexts: [emoji, {...coffee, word: 'love', people: []}], cached: false});
  assert.equal($('context-tabs').children[0].textContent, '👨‍👩‍👧‍👦');
  assert.equal($('context-detail-heading').textContent, '👨‍👩‍👧‍👦');
  assert.ok($('context-comparison-chart').children.at(-1).children[1].textContent.includes('uses / 1,000 tokens'));
  context.renderSemantic({...data, person_ids: [], people: [], summary: {...summary, messages: 0, words: 0, diversity: null}, timeline: [], names: [], top_words: []});
  assert.equal($('semantic-trend').children[0].textContent, 'No data for this measure.');
  const beforePicker = calls.length;
  $('context-words').value = 'love, ❤';
  await $('context-emoji-button').listeners.click();
  assert.equal($('context-emoji-dialog').open, true);
  assert.equal(catalogLoads, 1);
  assert.equal($('context-emoji-grid').children.length, 72);
  $('context-emoji-more').listeners.click();
  assert.equal($('context-emoji-grid').children.length, 144);
  $('context-emoji-search').value = 'red heart';
  $('context-emoji-search').listeners.input();
  const choice = emoji => $('context-emoji-grid').children.find(button => button.textContent === emoji);
  assert.equal(choice('❤️').attributes['aria-pressed'], 'true');
  choice('❤️').listeners.click();
  assert.equal($('context-words').value, 'love');
  choice('❤️').listeners.click();
  assert.equal($('context-words').value, 'love, ❤️');
  $('context-emoji-search').value = 'thumbs up medium skin tone';
  $('context-emoji-search').listeners.input();
  choice('👍🏽').listeners.click();
  assert.equal($('context-words').value, 'love, ❤️, 👍🏽');
  $('context-emoji-search').value = '👨‍👩‍👧‍👦';
  $('context-emoji-search').listeners.input();
  choice('👨‍👩‍👧‍👦').listeners.click();
  assert.equal($('context-words').value, 'love, ❤️, 👍🏽, 👨‍👩‍👧‍👦');
  $('context-words').value = 'one, two, three, four, five, ❤️';
  $('context-emoji-search').value = 'heart';
  $('context-emoji-search').listeners.input();
  assert.equal(choice('❤️').disabled, false);
  assert.ok($('context-emoji-grid').children.filter(button => button.textContent !== '❤️').every(button => button.disabled));
  choice('❤️').listeners.click();
  assert.equal(context.contextSelections().length, 5);
  $('context-emoji-search').value = 'nothing matches this xyz';
  $('context-emoji-search').listeners.input();
  assert.equal($('context-emoji-grid').children.length, 0);
  assert.equal($('context-emoji-more').hidden, true);
  $('context-emoji-search').value = '';
  $('context-emoji-group').value = 'Flags';
  $('context-emoji-group').listeners.change();
  assert.ok($('context-emoji-grid').children.every(button => state.contextEmojiData.find(item => item.emoji === button.textContent).group === 'Flags'));
  $('context-emoji-close').listeners.click();
  assert.equal($('context-emoji-dialog').open, false);
  assert.equal($('context-emoji-button').focused, true);
  await $('context-emoji-button').listeners.click();
  assert.equal(catalogLoads, 1, 'Catalog is reused on reopen');
  assert.equal(calls.length, beforePicker, 'Picker changes must not start analysis');
  console.log('Vocabulary UI checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
