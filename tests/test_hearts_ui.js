// Exercise heart charts and message previews using fictional data, without a browser demo.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor(tag, text = '') {
    this.tag = tag; this.textContent = text; this.children = []; this.attributes = {};
    this.style = {}; this.value = ''; this.listeners = {}; this.classList = {add() {}};
  }
  append(...children) {
    this.children.push(...children);
    if (this.children.length === children.length && children[0]?.tag === 'option') this.value = children[0].value;
  }
  replaceChildren(...children) { this.children = []; this.value = ''; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  set innerHTML(_) { throw new Error('Message content must only be rendered as text'); }
}
const elements = new Map();
const $ = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
const state = {};
const context = vm.createContext({state, $, number: String,
  displaySender: sender => sender === 'Me' ? 'You' : 'Alex',
  dateLabel: timestamp => timestamp || 'Unknown date',
  peopleColors: ['blue', 'red'],
  document: {createTextNode: text => new Element('text', text)},
  node: (tag, css, text = '') => new Element(tag, text),
  svgNode: (tag, attrs, text = '') => { const el = new Element(tag, text); el.attributes = attrs; return el; },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../pymessage/web/semantic.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../pymessage/web/heart-media.js'), 'utf8'), context);
const script = fs.readFileSync(path.join(__dirname, '../pymessage/web/app.js'), 'utf8');
vm.runInContext(script.slice(script.indexOf('function heartSenderName('), script.indexOf('function svgNode(')), context);
const data = {total: 4, message_count: 3, buckets: [1, 2],
  by_sender: {'a@example.invalid': {hearts: 2, messages: 2, buckets: {'1': 2}}, 'b@example.invalid': {hearts: 2, messages: 1, buckets: {'2': 1}}},
  periods: ['2024-01-01', '2024-01-02', '2024-01-03'], group_by: 'day', timezone: 'UTC',
  series: [{sender: 'a@example.invalid', values: [1, 0, 1]}, {sender: 'b@example.invalid', values: [0, 0, 2]}], period_totals: [1, 0, 3],
  definition: 'Current standard hearts.', top_messages: [
    {id: 'm1', sender: 'b@example.invalid', hearts: 2, timestamp: '2024-01-03', text: '<script>literal</script>', attachments: 0},
    {id: 'm2', sender: 'a@example.invalid', hearts: 1, timestamp: null, text: '', attachments: 1},
    {id: 'm3', sender: 'a@example.invalid', hearts: 1, timestamp: '2024-01-01', text: 'must not display', unsent: true, attachments: 0},
  ]};
context.renderHearts(data);
context.renderHearts({...data, matrix: {
  people: ['a@example.invalid', 'b@example.invalid', 'c@example.invalid'],
  message_counts: {'a@example.invalid': 4, 'b@example.invalid': 2},
  counts: {'a@example.invalid': {'b@example.invalid': 2}, 'b@example.invalid': {'a@example.invalid': 1}},
}});
const matrixRows = $('heart-matrix').children[0].children[1].children;
assert.equal(matrixRows[0].children[2].textContent, '100.0%');
assert.equal(matrixRows[1].children[1].textContent, '25.0%');
assert.match(matrixRows[1].children[1].title, /hearted 1 of .* 4 messages/);
assert.equal(matrixRows[0].children[1].textContent, '—', 'Exclude diagonal');
assert.equal(matrixRows[0].children[3].textContent, '—', 'No authored messages is undefined');
assert.equal(matrixRows[2].children[1].textContent, '0.0%', 'Zero hearts is defined');
assert.notEqual(matrixRows[0].children[2].style.backgroundColor, matrixRows[1].children[1].style.backgroundColor);
context.renderHearts(data);
assert.equal($('hearts-section').hidden, false);
assert.equal($('heart-summary').textContent, '4 hearts on 3 messages');
assert.equal($('heart-exact').value, '1');
assert.equal($('heart-buckets-chart').children[0].children[0].children[0].textContent, 'Alex · a@example.invalid');
assert.equal($('heart-buckets-chart').children[0].children[0].children[1].textContent, '2 messages');
$('heart-exact').value = '2'; $('heart-exact').listeners.change();
assert.equal($('heart-buckets-chart').children[0].children[0].children[0].textContent, 'Alex · b@example.invalid');
assert.equal($('heart-buckets-chart').children[0].children[0].children[1].textContent, '1 message');
const table = $('heart-buckets-table').children[0];
assert.equal(table.children[1].children[0].children[2].textContent, '0', 'Exact-count columns must not be cumulative');
assert.equal($('heart-top-messages').children[0].children[2].textContent, '<script>literal</script>');
assert.equal($('heart-top-messages').children[1].children[2].textContent, 'Attachment-only message.');
assert.equal($('heart-top-messages').children[2].children[2].textContent, 'Message was unsent.');
$('heart-chart-mode').value = 'people'; $('heart-chart-mode').listeners.change();
assert.equal($('heart-history').children[1].children.filter(el => el.tag === 'path').length, 2);
context.renderHearts({...data, total: 0, message_count: 0, buckets: [], top_messages: [], by_sender: {}, series: [], period_totals: [0, 0, 0]});
assert.equal($('heart-exact').disabled, true);
assert.equal($('heart-top-messages').children.length, 0);
assert.equal($('heart-top-empty').hidden, false);
context.renderHearts(null);
assert.equal($('hearts-section').hidden, true);
console.log('Group-chat heart UI checks passed.');
