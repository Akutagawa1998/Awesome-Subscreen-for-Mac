const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {dayKey, eventOnDay, uniqueEvents} = require('../web/time-utils.js');

const appSource = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const zoneSource = appSource.match(/^const (?:ny|calendarZone)\s*=.*$/gm).join('\n');
const calendarSource = appSource.slice(appSource.indexOf('const todayKey='), appSource.indexOf('async function loadCalendar'));
const controlsSource = appSource.slice(appSource.indexOf("$('#month-grid').addEventListener"), appSource.indexOf("$('#calendar-settings').addEventListener"));

function calendarFixture(events = []) {
  const now = Date.parse('2026-10-17T16:30:00Z'), nodes = new Map();
  class TestDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const get = selector => {
    if (!nodes.has(selector)) {
      const listeners = new Map();
      nodes.set(selector, {
        innerHTML: '', textContent: '',
        addEventListener(type, fn) { listeners.set(type, fn); },
        click() { listeners.get('click')?.(); }
      });
    }
    return nodes.get(selector);
  };
  let loads = 0;
  const context = vm.createContext({
    Date: TestDate, Intl, dayKey, eventOnDay, uniqueEvents, $: get,
    esc: value => String(value ?? ''),
    calendarData: {events, errors: [], fetchedAt: 'synthetic'},
    googleConfig: {accounts: [{id: 'synthetic-account'}]}, calendarBusy: false,
    loadCalendar() { loads++; }
  });
  vm.runInContext(zoneSource + '\n' + calendarSource + '\n' + controlsSource, context, {filename: 'app.js:calendar'});
  return {
    get,
    render: () => vm.runInContext('renderCalendar()', context),
    choose: key => vm.runInContext(`chosenDate=${JSON.stringify(key)}`, context),
    state: () => JSON.parse(vm.runInContext('JSON.stringify({chosenDate,today:todayKey(),calendarToday:calendarTodayKey(),ny,calendarZone})', context)),
    loads: () => loads
  };
}

function timed(id, start, end) {
  return {id, title: id, start: {dateTime: start}, end: {dateTime: end}, account: 'Synthetic account', color: '#123456'};
}

function article(html, title) {
  return [...html.matchAll(/<article\b[^>]*>[\s\S]*?<\/article>/g)].map(match => match[0]).find(value => value.includes(`<h3>${title}</h3>`));
}

function dayButton(html, key) {
  return [...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(match => match[0]).find(value => value.includes(`data-day="${key}"`));
}

test('calendar initialization and Today use Shanghai while the shared todayKey stays New York', () => {
  const h = calendarFixture();
  h.render();
  assert.deepEqual(h.state(), {
    chosenDate: '2026-10-18', today: '2026-10-17', calendarToday: '2026-10-18',
    ny: 'America/New_York', calendarZone: 'Asia/Shanghai'
  });
  assert.match(dayButton(h.get('#month-grid').innerHTML, '2026-10-18'), /class="[^"]*\btoday\b/);
  assert.doesNotMatch(dayButton(h.get('#month-grid').innerHTML, '2026-10-17'), /class="[^"]*\btoday\b/);
  assert.match(h.get('#agenda-day').textContent, /10月18日/);
  h.choose('2026-10-01');
  h.get('#month-today').click();
  assert.equal(h.state().chosenDate, '2026-10-18');
  assert.equal(h.loads(), 1);
  assert.equal(h.state().today, '2026-10-17', 'Codex daily bucket selection must retain the shared New York date');
});

test('actual calendar rendering groups and formats Shanghai dates, with all-day first and mixed offsets in real order', () => {
  const h = calendarFixture([
    timed('later-utc', '2026-10-18T03:00:00Z', '2026-10-18T03:30:00Z'),
    timed('earlier-offset', '2026-10-18T10:00:00+08:00', '2026-10-18T10:30:00+08:00'),
    {id: 'all-day', title: 'all-day', start: {date: '2026-10-18'}, end: {date: '2026-10-19'}},
    timed('shanghai-midnight', '2026-10-17T16:30:00Z', '2026-10-17T17:00:00Z')
  ]);
  h.render();
  const agenda = h.get('#agenda-list').innerHTML;
  assert.match(article(agenda, 'shanghai-midnight'), /00:30 — 01:00/);
  assert.match(article(agenda, 'earlier-offset'), /10:00 — 10:30/);
  assert.match(article(agenda, 'later-utc'), /11:00 — 11:30/);
  assert.ok(agenda.indexOf('<h3>all-day</h3>') < agenda.indexOf('<h3>shanghai-midnight</h3>'), 'all-day rows precede timed rows');
  assert.ok(agenda.indexOf('<h3>earlier-offset</h3>') < agenda.indexOf('<h3>later-utc</h3>'), 'raw ISO lexical order must not invert real timestamps');
  assert.match(dayButton(h.get('#month-grid').innerHTML, '2026-10-18'), /event-dot/);
  assert.doesNotMatch(dayButton(h.get('#month-grid').innerHTML, '2026-10-17'), /event-dot/);
});

test('actual calendar rendering uses Shanghai for overnight labels and excludes a midnight end from the next day', () => {
  const h = calendarFixture([
    timed('from-previous', '2026-10-17T23:30:00+08:00', '2026-10-18T00:30:00+08:00'),
    timed('to-next', '2026-10-18T23:30:00+08:00', '2026-10-19T01:00:00+08:00'),
    timed('midnight-end', '2026-10-18T23:00:00+08:00', '2026-10-19T00:00:00+08:00')
  ]);
  h.render();
  let agenda = h.get('#agenda-list').innerHTML;
  assert.match(article(agenda, 'from-previous'), /前一天 — 00:30/);
  assert.match(article(agenda, 'to-next'), /23:30 — 次日 01:00/);
  assert.match(article(agenda, 'midnight-end'), /23:00 — 次日 00:00/);
  h.choose('2026-10-19');
  h.render();
  agenda = h.get('#agenda-list').innerHTML;
  assert.match(article(agenda, 'to-next'), /前一天 — 01:00/);
  assert.equal(article(agenda, 'midnight-end'), undefined);
  assert.equal(article(agenda, 'from-previous'), undefined);
});
