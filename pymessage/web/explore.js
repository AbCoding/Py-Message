'use strict';

const themeKey = 'pymessage-theme';
try {
  if (localStorage.getItem(themeKey) === 'studio') document.documentElement.dataset.theme = 'studio';
} catch { /* Private browsing can disable local storage. */ }


const pageNames = {overview: 'Overview', direct: 'Direct texts', groups: 'Group chats'};
const measureNames = {activity: 'Activity', language: 'Language', reply: 'Reply timing'};
const scopeNames = {overview: 'All conversations', direct: 'All direct texts', groups: 'All group chats'};
const work = {route: null, target: null, serial: 0, results: new Map(), details: new Map(), views: {}, ready: false};

function baseQuery(page = 'overview') {
  return {page, target: '', measure: 'activity', direction: 'all', author: '', gap: 24, language_stat:'length', term:'',
    group_by: 'month', start_date: '', end_date: '', timezone: state.timezone};
}
function queryKey(query) {
  return JSON.stringify(query, Object.keys(query).sort());
}
function calendarDay(value, days) {
  const date = new Date(value + 'T12:00:00Z');
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
function optionList(select, items, selected) {
  select.replaceChildren(...items.map(([value, label]) => {
    const item = node('option', '', label); item.value = value; return item;
  }));
  select.value = selected;
}
function action(label, callback, style = 'outlined') {
  const button = node('button', style, label); button.type = 'button';
  button.addEventListener('click', safe(callback)); return button;
}
function toggleLibrary(open) {
  document.querySelector('.app-shell').classList.toggle('library-open', open);
  $('toggle-library').setAttribute('aria-expanded', String(open));
}
function routeState(route) {
  return {explore: route, depth: history.state?.depth || 0};
}
function rememberRoute() {
  if (work.route) history.replaceState(routeState(work.route), '');
}
function updateQuery(fields) {
  Object.assign(work.route.query, fields);
  rememberRoute();
}
function rememberPosition() {
  if (!work.route) return;
  const panel = work.route.panel;
  if (['activity','semantic','context'].includes(panel)) {
    const data = state[panel === 'context' ? 'wordContext' : panel];
    if (data) {
      const key = work.route.detailKey || String(work.serial);
      work.details.set(key,data);
      if (work.details.size > 8) work.details.delete(work.details.keys().next().value);
      work.route.detailKey = key; rememberRoute();
    }
    return;
  }
  if (panel || $('explorer').hidden) return;
  const slider = $('work-output').querySelector('input[type="range"]');
  const compare = $('work-output').querySelector('details');
  work.route.position = {period:slider?.value, compare:compare?.open,
    values:compare ? [...compare.querySelectorAll('select')].map(el => el.value) : [],
    scroll:$('explorer').scrollTop};
  rememberRoute();
}
async function navigateWork(query, panel = '', push = true, entry = null) {
  if (push) rememberPosition();
  const route = {query: {...baseQuery(query.page), ...query}, panel};
  if (entry) route.entry = {id: entry.id, kind: entry.kind || 'chat'};
  if (push) history.pushState({explore: route, depth: (history.state?.depth || 0) + 1}, '');
  await showWorkRoute(route);
}
async function showWorkRoute(route) {
  const serial = ++work.serial;
  const q = route.query = {...baseQuery(route.query.page), ...route.query};
  work.route = route;
  work.target = null;
  $('work-output').replaceChildren();
  toggleLibrary(false);
  clearSelection();
  document.querySelector('.app-shell').dataset.panel = route.panel;
  $('report-heading').textContent = route.panel === 'reactions' ? 'Group reactions' : 'Analysis';
  $('reaction-view-label').hidden = route.panel !== 'reactions';
  if (route.panel === 'reactions') {
    $('reaction-view').value = route.reactionView || 'totals';
    focusReaction();
  } else {
    document.querySelectorAll('[data-heart-view], .reactions-card').forEach(el => {el.hidden = false;});
  }
  $('explorer').hidden = true;
  $('welcome').hidden = work.ready;
  for (const tab of $('pages').querySelectorAll('button')) {
    if (tab.dataset.page === q.page) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }
  const mode = q.page === 'direct' ? 'people' : 'conversations';
  if (state.libraryMode !== mode || state.listPage !== q.page) {
    state.listPage = q.page;
    await setLibraryMode(mode);
    if (serial !== work.serial) return;
  }
  $('library-heading').textContent = {overview: 'Conversations', direct: 'People', groups: 'Groups'}[q.page];
  $('toggle-library').textContent = {overview:'Library', direct:'People', groups:'Groups'}[q.page];
  $('all-scope').hidden = !q.target && !route.panel;
  $('all-scope').textContent = scopeNames[q.page];
  $('library-label').textContent = {overview: 'RECENT CONVERSATIONS', direct: 'DIRECT CORRESPONDENTS', groups: 'GROUP CONVERSATIONS'}[q.page];
  $('search').placeholder = {overview: 'Search conversations', direct: 'Search people or addresses', groups: 'Search group chats'}[q.page];
  $('search').setAttribute('aria-label', $('search').placeholder);
  if (!work.ready) { $('welcome').hidden = false; return; }
  $('welcome').hidden = true;
  const id = route.entry?.id || q.target;
  if (id) {
    const person = route.entry ? route.entry.kind === 'person' : q.page === 'direct';
    let detail;
    try {
      detail = await json((person ? '/api/person?' : '/api/chat?') + new URLSearchParams({id}));
    } catch (error) {
      if (serial !== work.serial) return;
      if (error.status !== 400) throw error;
      await navigateWork({...baseQuery(q.page),...inheritedRange()}, '', false);
      rememberRoute(); notify(error.message, true); return;
    }
    if (serial !== work.serial) return;
    work.target = person ? personEntry(detail) : detail;
  }
  if (route.panel) {
    if (['messages', 'report', 'reactions'].includes(route.panel)) {
      await selectChat(work.target);
      if (serial !== work.serial) return;
      if (route.panel !== 'messages') {
        showTab('analysis');
        $('start-date').value = q.start_date; $('end-date').value = q.end_date; $('group-by').value = q.group_by;
        const saved = state.results.get(id);
        if (saved && ((saved.config.start_date || '') !== q.start_date || (saved.config.end_date || '') !== q.end_date || saved.config.group_by !== q.group_by)) {
          $('results').hidden = true; $('analysis-empty').hidden = false;
        }
        if (route.panel === 'reactions' && $('results').hidden && !state.busy) await runAnalysis({preventDefault() {}});
      }
    } else {
      const prefix = route.panel;
      const selected = q.target && work.target?.kind === 'person' ? [[q.target,work.target]] : [];
      if (prefix === 'activity') openPeopleActivity();
      if (prefix === 'semantic') openSemantic();
      if (prefix === 'context') openWordContext();
      // A vocabulary word carries its own selection into the context panel.
      if (prefix === 'semantic' || prefix === 'context') {
        state[prefix + 'Selected'] = new Map(selected);
        renderSemanticSelection(prefix);
        invalidatePersonAnalysis(prefix);
      }
      $(prefix + '-start').value = q.start_date; $(prefix + '-end').value = q.end_date;
      $(prefix + '-group').value = q.group_by;
      if (prefix === 'activity') {
        invalidateActivity(); $('activity-metric').value = q.direction === 'all' ? 'total' : q.direction;
      }
      const previous = work.details.get(route.detailKey);
      if (previous) ({semantic:renderSemantic, context:renderWordContext, activity:renderPeopleActivity})[prefix](previous);
    }
    return;
  }
  $('explorer').hidden = false;
  $('explorer').scrollTop = 0;
  $('explore-title').textContent = work.target?.name || pageNames[q.page];
  $('explore-scope').textContent = work.target ? pageNames[q.page] : scopeNames[q.page];
  $('explore-back').hidden = !history.state?.depth;
  $('browse-target').hidden = !q.target;
  $('open-activity').hidden = q.page !== 'direct' || !!q.target || q.measure !== 'activity';
  $('open-semantic').hidden = $('open-context').hidden = q.page !== 'direct' || q.measure !== 'language' || q.direction !== 'sent';
  $('open-report').hidden = !q.target || q.page !== 'groups';
  const measures = ['activity', 'language'];
  if (q.target) measures.push(q.page === 'direct' ? 'reply' : 'reactions');
  $('measure-tabs').replaceChildren(...measures.map(key => {
    const button = action(measureNames[key] || 'Reactions', () => {
      if (key === 'reactions') return navigateWork(q, key);
      if (key !== q.measure) return navigateWork({...q, measure:key, language_stat:'length', term:'', direction:key === 'language' ? 'sent' : 'all', author:''});
    });
    button.setAttribute('aria-pressed', String(key === q.measure)); return button;
  }));
  optionList($('work-direction'), [['all','Sent and received'],['sent','Sent by me'],['received','Received']], q.direction);
  $('language-label').hidden = q.measure !== 'language';
  $('work-language').value = q.language_stat;
  $('term-label').hidden = q.measure !== 'language' || q.language_stat !== 'term';
  $('work-term').value = q.term;
  $('work-term').required = !$('term-label').hidden;
  $('direction-label').hidden = q.measure === 'reply' || !!q.author;
  $('gap-label').hidden = q.measure !== 'reply';
  $('work-gap').value = String(q.gap); $('work-group').value = q.group_by;
  $('work-start').value = q.start_date; $('work-end').value = q.end_date ? calendarDay(q.end_date, -1) : '';
  $('work-options').open = Boolean(q.start_date || q.end_date || q.gap !== 24);
  $('work-zone').textContent = q.timezone;
  $('author-label').hidden = q.page !== 'groups' || !q.target;
  if (!$('author-label').hidden) {
    const members = await json('/api/group-members?' + new URLSearchParams({id:q.target}));
    if (serial !== work.serial) return;
    const names = members.map(m => m.name);
    optionList($('work-author'), [['','Everyone'],['me','You'], ...members.map(m => [m.id,
      names.filter(name => name === m.name).length > 1 ? m.name + ' · ' + m.addresses[0] : m.name])], q.author);
  }
  renderChats();
  renderWorkOutput();
  $('explorer').scrollTop = route.position?.scroll || 0;
  drawSavedViews();
}

function readWorkFilters() {
  const end = $('work-end').value;
  const author = $('author-label').hidden ? '' : $('work-author').value;
  const languageStat = work.route.query.measure === 'language' ? $('work-language').value : 'length';
  return {language_stat:languageStat, term:languageStat === 'term' ? $('work-term').value.trim() : '', group_by:$('work-group').value, direction: author || work.route.query.measure === 'reply' ? 'all' : $('work-direction').value,
    start_date:$('work-start').value, end_date:end ? calendarDay(end, 1) : '',
    gap:Number($('work-gap').value), author};
}
function recordWorkFilters() {
  const filters = readWorkFilters();
  if (Object.entries(filters).some(([k,v]) => v !== work.route.query[k])) delete work.route.position;
  updateQuery(filters);
  $('term-label').hidden = work.route.query.measure !== 'language' || filters.language_stat !== 'term';
  $('work-term').required = !$('term-label').hidden;
  $('open-semantic').hidden = $('open-context').hidden = work.route.query.page !== 'direct' || work.route.query.measure !== 'language' || filters.direction !== 'sent';
  $('direction-label').hidden = !!work.route.query.author || work.route.query.measure === 'reply';
  renderWorkOutput();
}
async function runWork() {
  if (state.busy) return;
  recordWorkFilters();
  const requested = queryKey(work.route.query);
  const result = await runJob('explore', {...work.route.query});
  // The server normalizes words and emoji presentation variants.
  if (work.route && !work.route.panel && queryKey(work.route.query) === requested) {
    updateQuery(result.query); $('work-term').value = result.query.term; renderWorkOutput();
  }
}
function acceptWorkResult(result) {
  const key = queryKey(result.query);
  work.results.set(key, result);
  if (work.results.size > 24) work.results.delete(work.results.keys().next().value);
  if (work.route && !work.route.panel && key === queryKey(work.route.query)) renderWorkOutput();
}
function card(title) {
  const section = node('section','card'); section.append(node('h3','',title)); return section;
}
function periodRange(q, period) {
  let end;
  if (q.group_by === 'month') {
    const next = new Date(period+'T12:00:00Z'); next.setUTCMonth(next.getUTCMonth()+1);
    end = next.toISOString().slice(0,10);
  } else end = calendarDay(period, q.group_by === 'week' ? 7 : 1);
  return {start_date:q.start_date && q.start_date > period ? q.start_date : period,
    end_date:q.end_date && q.end_date < end ? q.end_date : end};
}
async function drillScope(id, range = {}) {
  const q = work.route.query;
  const page = q.page === 'overview' ? id : q.page;
  if (!(page in pageNames)) return;
  await navigateWork({...q, ...range, page, target:q.page === 'overview' ? '' : id, author:''});
  if (!state.busy) await runWork();
}
function renderWorkOutput() {
  const q = work.route.query, out = $('work-output');
  const result = work.results.get(queryKey(q));
  out.replaceChildren();
  $('work-apply').textContent = result ? 'Update analysis' : 'Run analysis';
  if (!result) {
    if (q.page === 'overview' && work.counts) {
      const stats = node('div','stats');
      stats.append(metric('Direct conversations',number(work.counts.direct)),metric('Group chats',number(work.counts.groups)));
      if (work.counts.unknown) stats.append(metric('Unclassified chats',number(work.counts.unknown)));
      out.append(stats);
    }
    const hints = {
      overview:'Compare direct texts and group chats across your archive.',
      direct:q.target ? 'Explore both sides of your direct conversations with this person.' : 'Compare activity and language across your direct correspondents.',
      groups:q.target ? 'Compare members of this group over time.' : 'Compare your group conversations over time.'
    };
    out.append(node('p','empty-list', hints[q.page])); return;
  }
  if (!result.periods.length) { out.append(node('p','empty-list','No matching messages. Try a wider date range or a different selection.')); return; }
  const languageUnit = {length:'words per text message',emoji:'emoji per 1,000 tokens',term:q.term + ' per 1,000 tokens'}[q.language_stat];
  const unit = {activity:'messages', reply:'seconds per reply (median)', language:languageUnit}[q.measure];
  const titles = {activity:'Messages over time', reply:'Median reply time by sender', language:{length:'Message length over time',emoji:'Emoji usage over time',term:q.term+' over time'}[q.language_stat]};
  const chart = card(titles[q.measure]), plot = node('div');
  const countLabel = {activity:'message', reply:'qualifying reply', language:'text message'}[q.measure];
  const units = result.total === 1 ? countLabel : q.measure === 'reply' ? 'qualifying replies' : countLabel + 's';
  const through = q.end_date && calendarDay(q.end_date,-1);
  let range = 'All history';
  if (q.start_date && through) range = q.start_date + ' – ' + through;
  else if (q.start_date) range = 'From ' + q.start_date;
  else if (through) range = 'Through ' + through;
  chart.append(node('p','card-note',number(result.total) + ' ' + units + ' · ' + range));
  chart.classList.add('trend-card');
  chart.append(plot); out.append(chart);
  semanticChart(plot, result.periods, result.series, unit);
  const slider = plot.querySelector('input');
  if (slider && work.route.position?.period != null) {
    slider.value = work.route.position.period; slider.dispatchEvent(new Event('input'));
  }
  const selectedPeriod = () => result.periods[Number(plot.querySelector('input')?.value || 0)];
  const actions = node('div','chart-actions');
  actions.append(action(q.measure === 'language' && q.language_stat !== 'length' ? 'View matching messages' : 'View messages in period', () => inspectPeriod(selectedPeriod())));
  if (q.page === 'overview') for (const page of ['direct','groups']) {
    actions.append(action(pageNames[page] + ' in period', () => drillScope(page,periodRange(q,selectedPeriod())), 'text-button'));
  }
  chart.append(actions);
  const canDrill = !q.target;
  const ranking = card(q.target ? 'By author' : {overview:'By conversation type',direct:'By person',groups:'By group'}[q.page]);
  const table = node('div','reaction-table');
  const cols = [q.target ? 'Author' : {overview:'Conversation type',direct:'Person',groups:'Group'}[q.page],q.measure === 'reply' ? 'Qualifying replies' : 'Messages'];
  if (q.measure !== 'activity') cols.push(q.measure === 'reply' ? 'Median reply time' : languageUnit);
  semanticTable(table, cols, result.ranking.map(r => [r.name, number(r.count),
    ...(q.measure === 'activity' ? [] : [q.measure === 'reply' ? duration(r.value) : semanticValue(r.value)])]));
  const headers = table.querySelectorAll('tbody th');
  result.ranking.forEach((r,i) => {
    if (canDrill && (q.page !== 'overview' || r.id === 'direct' || r.id === 'groups')) {
      headers[i].replaceChildren(action(r.name, () => drillScope(r.id),'text-button'));
    }
  });
  ranking.classList.add('ranking-card');
  ranking.append(table); out.append(ranking);
  if (q.measure !== 'language' && result.periods.length > 1) renderComparison(out,result,unit);
  if (q.measure === 'language') {
    const vocabulary = node('div', 'vocabulary-grid');
    for (const [title, entries] of [['Frequent words',result.words],['Emoji',result.emoji]]) {
      const section = card(title);
      const list = node('div', 'frequency-list');
      for (const [word,count] of entries) {
        const button = action('', async () => {
          if (state.busy) return;
          await navigateWork({...q, language_stat:'term', term:word}); await runWork();
        },'word-chip');
        button.append(node('span','',word), node('b','',number(count)));
        button.title = 'Plot usage over time'; list.append(button);
      }
      if (entries.length) section.append(list);
      else section.append(node('p','card-note','None in this selection.'));
      vocabulary.append(section);
    }
    if (q.language_stat === 'length') out.insertBefore(vocabulary, chart);
    else out.append(vocabulary);
  }
  out.append(node('p','coverage',q.measure === 'reply' ? 'Alternating senders within each direct thread. The maximum gap applies before computing the median.' :
    'Each dated message counts once across the archive. Reactions and system events are excluded. The chart shows up to ten series by message count.'));
}
function renderComparison(out, result, unit) {
  const panel = node('details','card'), controls = node('div','work-row'), table = node('div','reaction-table');
  panel.append(node('summary','','Compare two periods'));
  const dates = result.periods.map((p,i) => [String(i),p]);
  const inputs = ['Earlier period','Later period'].map((text,i) => {
    const label = node('label','',text), select = node('select');
    optionList(select,dates,String(dates.length-2+i)); label.append(select); controls.append(label); return select;
  });
  const draw = () => {
    const [a,b] = inputs.map(input => Number(input.value));
    const values = result.series.map(s => {
      const x = s.values[a], y = s.values[b];
      return [s.name,x ?? '—',y ?? '—',x == null || y == null ? '—' : semanticValue(y-x)];
    });
    semanticTable(table,[unit, result.periods[a], result.periods[b],'Change'],values);
  };
  if (work.route.position?.compare) {
    panel.open = true;
    inputs.forEach((input,i) => {input.value = work.route.position.values[i];});
  }
  inputs.forEach(input => input.addEventListener('change',draw)); draw();
  panel.append(controls,table); out.append(panel);
}
async function inspectPeriod(period) {
  if (state.busy) return;
  const dialog = $('inspect-dialog'), list = $('inspect-list');
  list.replaceChildren(); $('inspect-title').textContent = 'Messages · ' + period; dialog.showModal();
  const query = {...work.route.query,period};
  let offset = 0;
  const more = $('inspect-more');
  const load = async () => {
    more.disabled = true; more.hidden = false; more.textContent = 'Loading…';
    try {
      const data = await runJob('inspect',{...query,offset});
      for (const m of data.messages) {
        const item = node('article','inspect-item');
        item.append(node('strong','',m.sender + ' · ' + m.name),node('p','',m.text || 'No displayable text'),
          node('small','',dateLabel(m.time,{dateStyle:'medium',timeStyle:'short'})));
        list.append(item);
      }
      offset += data.messages.length; more.hidden = !data.has_more;
      if (!offset) list.append(node('p','','No matching messages.'));
    } finally {more.disabled=false; more.textContent='More messages';}
  };
  more.onclick = safe(load); await load();
}
function drawSavedViews() {
  const list = $('view-list'); list.replaceChildren();
  for (const [name,q] of Object.entries(work.views)) {
    const row = node('div','saved-row');
    row.append(action(name, async () => {
      await navigateWork(q); if (!state.busy) await runWork();
    },'text-button'), action('Remove', async () => {
      work.views = await json('/api/views',{name,delete:true}); drawSavedViews();
    },'text-button'));
    list.append(row);
  }
}
async function selectWorkEntry(entry) {
  const current = work.route.query;
  const shared = {measure:current.measure === 'reply' && entry.kind !== 'person' ? 'activity' : current.measure,
    language_stat:current.language_stat, term:current.term, direction:current.direction, ...inheritedRange()};
  if (entry.kind === 'person') return navigateWork({...baseQuery('direct'), target:entry.id, ...shared});
  if (entry.scope === 'groups') return navigateWork({...baseQuery('groups'), target:entry.id, ...shared});
  return navigateWork({...baseQuery(work.route.query.page), ...inheritedRange()}, 'messages', true, entry);
}
function inheritedRange() {
  const q = work.route?.query || baseQuery();
  return {start_date:q.start_date, end_date:q.end_date, group_by:q.group_by};
}
function backToWork() {
  if (history.state?.depth) history.back();
  else safe(() => navigateWork(baseQuery(work.route?.query.page)))();
}
async function initializeWork(status) {
  work.ready = status.archive_exists;
  [work.views, work.counts] = await Promise.all([json('/api/views'),json('/api/library-counts')]);
  const route = history.state?.explore || {query:baseQuery(),panel:''};
  history.replaceState(routeState(route),'');
  await showWorkRoute(route);
}
function focusReaction() {
  const selected = $('reaction-view').value;
  document.querySelector('.reactions-card').hidden = selected !== 'totals';
  document.querySelectorAll('[data-heart-view]').forEach(el => {el.hidden = el.dataset.heartView !== selected;});
}
function bindWork() {
  const theme = $('app-theme');
  theme.value = document.documentElement.dataset.theme;
  theme.addEventListener('change', () => {
    document.documentElement.dataset.theme = theme.value;
    try { localStorage.setItem(themeKey, theme.value); }
    catch { /* The selection still applies for this visit. */ }
  });
  $('reaction-view').addEventListener('change',() => {
    work.route.reactionView = $('reaction-view').value; rememberRoute(); focusReaction();
  });
  $('pages').addEventListener('click',safe(event => {
    const page = event.target.closest('button')?.dataset.page;
    if (page) return navigateWork({...baseQuery(page), ...inheritedRange()});
  }));
  window.addEventListener('popstate',safe(() => showWorkRoute(history.state?.explore || {query:baseQuery(),panel:''})));
  $('toggle-library').addEventListener('click',() => toggleLibrary($('toggle-library').getAttribute('aria-expanded') !== 'true'));
  $('all-scope').addEventListener('click',safe(() => navigateWork({...baseQuery(work.route.query.page),...inheritedRange()})));
  $('browse-target').addEventListener('click',safe(() => navigateWork(work.route.query,'messages')));
  $('open-report').addEventListener('click',safe(() => navigateWork(work.route.query,'report')));
  $('explore-back').addEventListener('click',backToWork);
  $('explore-form').addEventListener('change',recordWorkFilters);
  $('explore-form').addEventListener('submit',e => {e.preventDefault(); safe(runWork)();});
  $('save-view').addEventListener('submit',e => {
    e.preventDefault();
    safe(async () => {
      recordWorkFilters();
      work.views = await json('/api/views',{...work.route.query, name:$('view-name').value});
      $('view-name').value=''; drawSavedViews(); notify('View saved.');
    })();
  });
  $('inspect-dialog').addEventListener('close',() => { $('inspect-list').replaceChildren(); });
  $('inspect-close').addEventListener('click',() => $('inspect-dialog').close());
}
