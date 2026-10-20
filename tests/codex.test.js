const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function fragment(start, end) {
  const first = appSource.indexOf(start), last = appSource.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `app.js fragment is available: ${start}`);
  return appSource.slice(first, last);
}
const statusSource = fragment('function statusInfo(', 'async function refreshStatus(');
const detailSource = fragment('function renderDetail(', 'function reviewApproval(');

const syntheticThreads = [
  {id: 'synthetic-active', name: 'Synthetic active', status: {type: 'active'}, updatedAt: 3},
  {id: 'synthetic-waiting', name: 'Synthetic waiting', status: {type: 'active', activeFlags: ['waitingOnApproval']}, updatedAt: 2},
  {id: 'synthetic-idle', preview: 'Synthetic preview\nSecond line', status: {type: 'idle'}, updatedAt: 1}
];
const syntheticApproval = {
  id: 'synthetic-request', method: 'item/commandExecution/requestApproval',
  params: {threadId: 'synthetic-active', reason: 'Synthetic approval', command: 'echo synthetic'}
};

function status(overrides = {}) {
  return structuredClone({
    connected: true, stale: false, connectionMode: 'control', hidden: [],
    threads: syntheticThreads, approvals: [syntheticApproval], fetchedAt: 1792369800,
    limits: {rateLimits: {limitId: 'codex', planType: 'pro', primary: {usedPercent: 42, windowDurationMins: 300}}},
    usage: {dailyUsageBuckets: [{startDate: '2026-10-18', tokens: 1200}]}, ...overrides
  });
}
function detail(overrides = {}) {
  return structuredClone({
    connected: true, stale: false, connectionMode: 'control',
    thread: syntheticThreads[0], messages: [{type: 'agent', text: 'Synthetic saved message'}],
    approvals: [syntheticApproval], attached: false, ...overrides
  });
}

function codexFixture(initialStatus = status(), initialDetail = null) {
  const nodes = new Map(), notices = [];
  let focusInDetail = false;
  const fixed = new Set(['#codex-connection', '#hidden-count', '#plan-name', '#usage-source', '#codex-usage',
    '#session-counts', '#session-coverage', '#session-list', '#agent-detail', '#approval-dialog']);
  const get = selector => {
    if (!fixed.has(selector) && !nodes.get('#agent-detail')?.innerHTML.includes(`id="${selector.slice(1)}"`)) return null;
    if (!nodes.has(selector)) {
      const classes = new Set();
      nodes.set(selector, {
        innerHTML: '', textContent: '', closeCount: 0,
        classList: {
          toggle(name, value) { value ? classes.add(name) : classes.delete(name); },
          contains(name) { return classes.has(name); }
        },
        addEventListener() {},
        contains() { return selector === '#agent-detail' && focusInDetail; },
        close() { this.closeCount++; }
      });
    }
    return nodes.get(selector);
  };
  const context = vm.createContext({
    Date, Intl, $: get, $$: () => [],
    document: {activeElement: {matches: selector => selector === 'textarea'}},
    esc: value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char])),
    compact: value => value == null ? '—' : String(value),
    todayKey: () => '2026-10-18', ny: 'America/New_York',
    window: {consoleUI: {quotaTone: () => 'green'}},
    statusData: initialStatus, detailData: initialDetail,
    selectedThread: initialDetail?.thread.id ?? null, detailKey: '', activeApproval: null,
    replyDrafts: {}, toast: value => notices.push(value),
    api() { throw new Error('Rendering must not issue API requests'); }
  });
  vm.runInContext(statusSource + '\n' + detailSource, context, {filename: 'app.js:codex'});
  return {
    get, notices,
    renderStatus: () => vm.runInContext('renderStatus()', context),
    renderDetail: () => vm.runInContext('renderDetail()', context),
    statusInfo: (thread, unavailable) => JSON.parse(vm.runInContext(`JSON.stringify(statusInfo(${JSON.stringify(thread)}, ${Boolean(unavailable)}))`, context)),
    setStatus: value => { context.statusData = value; },
    setDetail: value => { context.detailData = value; },
    focusComposer: () => { focusInDetail = true; },
    openApproval: () => { context.activeApproval = {...structuredClone(syntheticApproval), connectionGeneration: context.detailData?.connectionGeneration}; },
    activeApproval: () => context.activeApproval
  };
}

function assertUnavailableCounts(h, unknown = 3) {
  const html = h.get('#session-counts').innerHTML;
  assert.match(html, /<strong>—<\/strong>运行/);
  assert.match(html, /<strong>—<\/strong>待处理/);
  assert.ok(html.includes(`<strong>${unknown}</strong>未知`));
}
function assertReadOnlyDetail(h) {
  const html = h.get('#agent-detail').innerHTML;
  assert.match(html, /Synthetic saved message/);
  assert.match(html, /运行状态未知/);
  assert.match(html, /href="codex:\/\/threads\/synthetic-active"/);
  assert.doesNotMatch(html, /id="(?:quick-reply|send-reply|attach-thread)"|data-review=|class="approval-card"/);
}

test('local read-only status never treats CLI active or approval flags as desktop runtime evidence', () => {
  const h = codexFixture(status({connectionMode: 'local_readonly'}));
  h.renderStatus();
  assert.equal(h.get('#codex-connection').textContent, '本机只读连接');
  assert.equal(h.get('#usage-source').textContent, '终端 Codex 账号');
  assert.match(h.get('#codex-usage').innerHTML, /42%/);
  assertUnavailableCounts(h);
  const rows = h.get('#session-list').innerHTML;
  assert.equal((rows.match(/运行状态未知/g) || []).length, 3);
  assert.doesNotMatch(rows, /status-active|status-waiting|需要批准|运行中/);
  assert.match(rows, /Synthetic preview/);
  assert.doesNotMatch(rows, /Second line/);
  assert.equal(h.statusInfo(syntheticThreads[1], true).key, 'unknown');
});

test('read-only detail independently rejects mutable UI even when backend returns active and an approval', () => {
  for (const [statusMode, detailMode] of [['local_readonly', 'control'], ['control', 'local_readonly']]) {
    const h = codexFixture(status({connectionMode: statusMode}), detail({connectionMode: detailMode}));
    h.renderDetail();
    assertReadOnlyDetail(h);
    assert.match(h.get('#agent-detail').innerHTML, /只读连接/);
  }
});

test('disconnected or stale snapshots keep readable history but show unknown counts and a cache label', () => {
  for (const state of [{connected: false, stale: false}, {connected: true, stale: true}]) {
    const h = codexFixture(status(state), detail());
    h.renderStatus();
    assertUnavailableCounts(h);
    assert.match(h.get('#session-coverage').textContent, /^缓存 · /);
    assert.equal((h.get('#session-list').innerHTML.match(/运行状态未知/g) || []).length, 3);
    assertReadOnlyDetail(h);
    assert.match(h.get('#agent-detail').innerHTML, /连接已中断，显示上次读取的内容/);
  }
});

test('a failed detail fetch cannot leave controls available merely because the status poll is healthy', () => {
  for (const state of [{connected: false, stale: false}, {connected: true, stale: true}]) {
    const h = codexFixture(status(), detail(state));
    h.renderDetail();
    assertReadOnlyDetail(h);
    assert.match(h.get('#agent-detail').innerHTML, /上次读取的内容/);
  }
});

test('capability downgrade removes a focused reply composer and closes a pending approval without a new detail payload', () => {
  for (const nextStatus of [status({connectionMode: 'local_readonly'}), status({connected: false, stale: true})]) {
    const h = codexFixture(status(), detail());
    h.renderDetail();
    assert.match(h.get('#agent-detail').innerHTML, /id="quick-reply"/);
    assert.match(h.get('#agent-detail').innerHTML, /data-review="synthetic-request"/);
    h.focusComposer();
    h.openApproval();
    h.setStatus(nextStatus);
    h.renderStatus();
    assertReadOnlyDetail(h);
    assert.equal(h.get('#approval-dialog').closeCount, 1);
    assert.equal(h.activeApproval(), null);
  }
});

test('healthy control connection still renders confirmed statuses and explicit reply and approval actions', () => {
  const h = codexFixture(status(), detail());
  h.renderStatus();
  h.renderDetail();
  assert.equal(h.get('#codex-connection').textContent, '本地已连接');
  assert.equal(h.get('#usage-source').textContent, '账户用量');
  assert.match(h.get('#session-counts').innerHTML, /<strong>1<\/strong>运行/);
  assert.match(h.get('#session-counts').innerHTML, /<strong>1<\/strong>待处理/);
  const html = h.get('#agent-detail').innerHTML;
  assert.match(html, /id="quick-reply"/);
  assert.match(html, /id="send-reply"/);
  assert.match(html, /id="attach-thread"/);
  assert.match(html, /data-review="synthetic-request"/);
});

test('healthy polling preserves a focused composer while a real capability change forces a redraw', () => {
  const h = codexFixture(status(), detail());
  h.renderDetail();
  h.focusComposer();
  h.setDetail(detail({messages: [{type: 'agent', text: 'Synthetic newer message'}]}));
  h.renderDetail();
  assert.match(h.get('#agent-detail').innerHTML, /Synthetic saved message/);
  assert.doesNotMatch(h.get('#agent-detail').innerHTML, /Synthetic newer message/);
  h.setStatus(status({connectionMode: 'local_readonly'}));
  h.renderStatus();
  assert.match(h.get('#agent-detail').innerHTML, /Synthetic newer message/);
  assert.doesNotMatch(h.get('#agent-detail').innerHTML, /id="quick-reply"/);
});

test('same-mode control reconnection invalidates focused controls backed by an older connection generation', () => {
  const h = codexFixture(status({connectionGeneration: 1}), detail({connectionGeneration: 1}));
  h.renderStatus();
  h.renderDetail();
  assert.match(h.get('#agent-detail').innerHTML, /id="quick-reply"/);
  assert.match(h.get('#agent-detail').innerHTML, /id="attach-thread"/);
  assert.match(h.get('#agent-detail').innerHTML, /data-review="synthetic-request"/);
  h.focusComposer();

  // Both snapshots still say control/active; only the owning connection changed.
  h.openApproval();
  h.setStatus(status({connectionGeneration: 2}));
  h.renderStatus();
  assertReadOnlyDetail(h);
  assert.match(h.get('#agent-detail').innerHTML, /上次读取的内容/);
  assert.equal(h.get('#codex-connection').textContent, '本地已连接');
  assert.equal(h.get('#approval-dialog').closeCount, 1, 'A reused request id must not preserve an old approval dialog');
  assert.equal(h.activeApproval(), null);
});

test('offline empty cache shows a connection prompt rather than suggesting no sessions exist', () => {
  const h = codexFixture(status({connected: false, stale: true, threads: [], limits: null, usage: null}));
  h.renderStatus();
  assertUnavailableCounts(h, 0);
  assert.match(h.get('#session-list').innerHTML, /正在连接 Codex/);
  assert.doesNotMatch(h.get('#session-list').innerHTML, /这里还没有会话/);
  assert.match(h.get('#codex-usage').innerHTML, /额度暂不可用/);
});
