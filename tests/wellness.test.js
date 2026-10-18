const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const apiSource = appSource.match(/async function api\([^]*?\n}\n/)[0];
const wellnessSource = fs.readFileSync(path.join(__dirname, '../web/wellness.js'), 'utf8');
const start = Date.parse('2026-01-01T12:00:00Z');
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };

function clock() {
  let now = start, nextId = 0;
  const timers = new Map();
  class TestDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const schedule = (callback, delay, repeat) => {
    const id = ++nextId;
    timers.set(id, {callback, at: now + delay, repeat});
    return id;
  };
  return {
    Date: TestDate,
    now: () => now,
    setTimeout: (fn, delay) => schedule(fn, delay, 0),
    setInterval: (fn, delay) => schedule(fn, delay, delay),
    clearTimeout: id => timers.delete(id),
    clearInterval: id => timers.delete(id),
    async advance(ms) {
      const until = now + ms;
      while (true) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= until)
          .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        const [id, timer] = next;
        now = timer.at;
        if (timer.repeat) timer.at += timer.repeat;
        else timers.delete(id);
        timer.callback();
        await flush();
      }
      now = until;
      await flush();
    }
  };
}

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }
  emit(type) { for (const fn of this.listeners.get(type) || []) fn({type}); }
}

function documentFixture() {
  const nodes = new Map();
  class Element extends Events {
    constructor(id) {
      super(); this.id = id; this.writes = 0; this._html = ''; this._text = '';
      const classes = new Set();
      this.classList = {
        add: name => classes.add(name),
        remove: name => classes.delete(name),
        contains: name => classes.has(name),
        toggle(name, force) {
          const enabled = force === undefined ? !classes.has(name) : force;
          if (enabled) classes.add(name); else classes.delete(name);
          return enabled;
        }
      };
    }
    get innerHTML() { return this._html; }
    set innerHTML(html) {
      this._html = html; this._text = html.replace(/<[^>]+>/g, ''); this.writes++;
      for (const [id, node] of nodes) if (node.owner === this) nodes.delete(id);
      // The production health view updates these ID-bearing elements directly.
      for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
        const node = new Element(match[3]); node.owner = this;
        for (const name of (match[2].match(/\bclass="([^"]*)"/)?.[1] || '').split(/\s+/)) {
          if (name) node.classList.add(name);
        }
        nodes.set(node.id, node);
      }
    }
    get textContent() { return this._text; }
    set textContent(value) { this._text = String(value); }
  }
  for (const id of ['todo-content', 'refresh-todo', 'health-live', 'health-content',
    'stand-timer', 'stand-done', 'water-timer', 'water-done', 'wellness-settings',
    'wellness-dialog']) nodes.set(id, new Element(id));
  const document = new Events();
  document.querySelector = selector => nodes.get(selector.slice(1)) || null;
  return {document, get: id => nodes.get(id)};
}

function sample(at = start - 120000, value = 72) {
  const metric = n => ({value: n, at: new Date(at).toISOString(), source: 'Synthetic watch'});
  return {connected: true, mode: 'file', metrics: {
    heartRate: metric(value), hrv: metric(41), restingHeartRate: metric(58), sleepHours: metric(7.5)
  }};
}

function hanging(request) {
  return new Promise((resolve, reject) => {
    assert.ok(request.options.signal, 'health fetch needs a request-specific AbortSignal');
    request.options.signal.addEventListener('abort', () => {
      request.abortedAt = request.clock.now();
      reject(Object.assign(new Error('Synthetic abort'), {name: 'AbortError'}));
    }, {once: true});
  });
}

async function wellnessFixture(healthResponses) {
  const timers = clock(), dom = documentFixture(), window = new Events(), requests = [];
  const storage = new Map();
  const context = vm.createContext({
    ...timers, AbortController, window, document: dom.document, currentPage: 3, PAGE: {todo: 3},
    $: selector => dom.document.querySelector(selector),
    esc: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    toast() {},
    async fetch(url, options) {
      const request = {url, options, at: timers.now(), clock: timers}; requests.push(request);
      let data;
      if (url === '/api/wellness') {
        assert.ok(healthResponses.length, 'unexpected extra health request');
        const next = healthResponses.shift();
        data = typeof next === 'function' ? await next(request) : next;
      } else {
        assert.equal(url, '/api/todo');
        data = {connected: false};
      }
      return {ok: true, json: async () => data};
    }
  });
  vm.runInContext(apiSource, context, {filename: 'app.js:api'});
  vm.runInContext(wellnessSource, context, {filename: 'wellness.js'});
  await flush();
  return {...dom, window, clock: timers, requests, healthRequests: () => requests.filter(r => r.url === '/api/wellness')};
}

test('a hung health read aborts at 8 seconds and the next polling attempt recovers', async () => {
  const h = await wellnessFixture([sample(), hanging, sample(start + 19000, 76)]);
  assert.equal(h.healthRequests().length, 1);
  const content = h.get('health-content').innerHTML;
  h.window.emit('focus');
  await flush();
  const pending = h.healthRequests()[1];
  await h.clock.advance(7999);
  assert.equal(pending.options.signal.aborted, false);
  h.window.emit('focus');
  await flush();
  assert.equal(h.healthRequests().length, 2, 'focus cannot duplicate an in-flight read');
  await h.clock.advance(1);
  assert.equal(pending.abortedAt, start + 8000);
  assert.equal(h.get('health-live').textContent, '同步中断');
  assert.equal(h.get('health-content').innerHTML, content, 'the timeout retains the last metric values');
  assert.match(h.get('health-sample-time').textContent, /^2 分钟前/, 'the timeout retains the original sampling time');
  assert.match(h.get('health-sample-time').textContent, /超时/);
  await h.clock.advance(10000);
  assert.equal(h.healthRequests().length, 2, 'retry delay starts when the failed read finishes');
  await h.clock.advance(1000);
  assert.equal(h.healthRequests().length, 3, 'the timeout releases busy so polling can recover');
  assert.equal(h.get('health-live').textContent, '最近采样');
  assert.match(h.get('health-content').innerHTML, />76<small>bpm/);
  assert.doesNotMatch(h.get('health-sample-time').textContent, /超时|中断/);
  for (const request of h.requests.filter(r => r.url !== '/api/wellness')) {
    assert.equal(request.options.signal, undefined, 'health cancellation must not leak into To Do');
  }
});

for (const [name, failure, message] of [
  ['network rejection', () => {throw new Error('Synthetic offline');}, 'Synthetic offline'],
  ['successful HTTP response with backend error', {...sample(start + 62000, 999), error: 'Synthetic read failure'}, 'Synthetic read failure']
]) {
  test(`${name} retains the last metrics and their sampling times`, async () => {
    const previous = sample(), original = JSON.stringify(previous);
    const h = await wellnessFixture([previous, failure, sample(start + 62000, 79)]);
    const content = h.get('health-content').innerHTML;
    assert.match(h.get('health-sample-time').textContent, /^2 分钟前/);
    h.window.wellnessUI.pageChanged(false);
    await h.clock.advance(62000);
    h.window.wellnessUI.pageChanged(true);
    await flush();
    assert.equal(h.get('health-live').textContent, '同步中断');
    assert.equal(h.get('health-content').innerHTML, content, 'all last-known metric values remain visible');
    assert.equal(h.get('health-sample-time').textContent, `3 分钟前 · Synthetic watch · ${message}`);
    for (const id of ['health-hrv-time', 'health-resting-time', 'health-sleep-time']) {
      assert.equal(h.get(id).textContent, '3 分钟前 · Synthetic watch');
    }
    assert.equal(JSON.stringify(previous), original, 'the original metric timestamps are unchanged');
    assert.equal(h.get('health-heart-reading').classList.contains('fresh'), false);
    h.window.emit('focus');
    await flush();
    assert.equal(h.get('health-live').textContent, '最近采样');
    assert.match(h.get('health-content').innerHTML, />79<small>bpm/);
    assert.doesNotMatch(h.get('health-sample-time').textContent, /Synthetic offline|Synthetic read failure/);
  });
}

test('sampling ages cross the freshness threshold during a hung request without rebuilding the view', async () => {
  const h = await wellnessFixture([sample(start - 294000), hanging]);
  const content = h.get('health-content'), writes = content.writes;
  const reading = h.get('health-heart-reading');
  assert.equal(h.get('health-live').textContent, '最近采样');
  assert.equal(reading.classList.contains('fresh'), true);
  h.window.emit('focus');
  await flush();
  await h.clock.advance(5000);
  assert.equal(h.get('health-live').textContent, '最近采样');
  await h.clock.advance(1000);
  assert.equal(h.healthRequests()[1].options.signal.aborted, false, 'the read is still pending at the threshold');
  assert.equal(h.get('health-live').textContent, '等待新采样');
  assert.equal(h.get('health-live').classList.contains('fresh'), false);
  assert.equal(reading.classList.contains('fresh'), false);
  for (const id of ['health-sample-time', 'health-hrv-time', 'health-resting-time', 'health-sleep-time']) {
    assert.equal(h.get(id).textContent, '5 分钟前 · Synthetic watch');
  }
  assert.equal(content.writes, writes, 'the one-second age update must not replace health-content');
  assert.equal(h.get('health-heart-reading'), reading, 'the metric element is preserved');
});

test('api forwards an optional signal while preserving ordinary GET, POST and HTTP error behavior', async () => {
  const requests = [], signal = new AbortController().signal;
  const context = vm.createContext({fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: url !== '/api/error', json: async () => url === '/api/error' ? {error: 'Synthetic HTTP error'} : {ok: true}};
  }});
  vm.runInContext(apiSource, context, {filename: 'app.js:api'});
  await context.api('/api/status');
  await context.api('/api/todo/complete', {id: 'synthetic-task', completed: true});
  await context.api('/api/wellness', undefined, {signal});
  await context.api('/api/calendar');
  assert.equal(JSON.stringify(requests[0].options), JSON.stringify({cache: 'no-store'}));
  assert.equal(JSON.stringify(requests[1].options), JSON.stringify({method: 'POST', headers: {'Content-Type': 'application/json', 'X-Wokyis': '1'}, body: JSON.stringify({id: 'synthetic-task', completed: true})}));
  assert.equal(requests[2].options.signal, signal);
  assert.equal(requests[2].options.cache, 'no-store');
  assert.equal(requests[3].options.signal, undefined);
  await assert.rejects(context.api('/api/error'), /Synthetic HTTP error/);
});
