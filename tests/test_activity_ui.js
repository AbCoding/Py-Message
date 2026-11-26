// Small DOM fixture: exercise the actual chart functions without launching the app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.attributes = {}; this.style = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
}
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, new Element(id));
  return elements.get(id);
};
const state = {};
const context = vm.createContext({state, $, Set, Map,
  number: String,
  safe: fn => fn,
  document: {createTextNode: text => new Element('text', text)},
  node: (tag, className, text = '') => new Element(tag, text),
  svgNode: (tag, attrs, text = '') => { const el = new Element(tag, text); el.attributes = attrs; return el; },
});
const script = fs.readFileSync(require('node:path').join(__dirname, '../pymessage/web/app.js'), 'utf8');
vm.runInContext(script.slice(script.indexOf('function activityName('), script.indexOf('function downloadActivity(')), context);

const a = {id: 'a', name: 'Alex', addresses: ['a@example.invalid'], count: 14, sent: 10, received: 4};
const b = {id: 'b', name: 'Alex', addresses: ['b@example.invalid'], count: 5, sent: 2, received: 3};
const data = {ranking_mode: 'period', top: 1, group_by: 'day', metric: 'total', timezone: 'UTC',
  periods: ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04'], period_totals: [8, 5, 6, 0],
  series: [{...a, points: [{index: 0, count: 8}, {index: 2, count: 6}]}, {...b, points: [{index: 1, count: 5}]}],
  period_leaders: [[{id: 'a', count: 8}], [{id: 'b', count: 5}], [{id: 'a', count: 6}], []],
  ranking: [a, b], people_count: 2, total: 19, definition: 'Direct conversations only.'};
context.renderPeopleActivity(data);
const svg = $('people-chart').children[0];
const paths = svg.children.filter(el => el.tag === 'path');
assert.equal(paths.length, 2);
assert.equal((paths[0].attributes.d.match(/M/g) || []).length, 2);
assert.ok(!paths[0].attributes.d.includes('L'), 'Do not connect through a non-qualifying period');
assert.equal(svg.children.filter(el => el.tag === 'circle').length, 3);
assert.equal($('people-legend').children.length, 0, 'Empty periods have no leaders');
context.setActivityPeriod(0);
assert.equal($('people-legend').children.length, 1);
assert.match($('activity-readout').children[1].textContent, /1\. Alex · a@example.invalid: 8/);
const color = $('people-legend').children[0].children[0].style.background;
context.setActivityPeriod(1);
assert.match($('activity-readout').children[1].textContent, /b@example.invalid: 5/);
context.setActivityPeriod(2);
assert.equal($('people-legend').children[0].children[0].style.background, color);
state.activityHidden.add('a');
context.drawPeopleChart();
assert.match($('activity-readout').children[1].textContent, /6 \(hidden\)/);
assert.equal($('people-chart').children[0].children.filter(el => el.tag === 'circle').length, 1);

context.renderPeopleActivity({...data, ranking_mode: 'overall', period_leaders: [], series: [{...a, values: [8, 0, 6, 0]}]});
assert.equal(($('people-chart').children[0].children.find(el => el.tag === 'path').attributes.d.match(/L/g) || []).length, 3);
context.renderPeopleActivity({...data, series: [], periods: [], period_leaders: [], ranking: [], total: 0});
assert.equal($('people-chart').children[0].textContent, 'No matching direct messages in this date range.');
console.log('People activity chart checks passed.');
