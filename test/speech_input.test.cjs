const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];

// Exercise the shipped page, not a rewritten controller. Only browser/OS boundaries are faked.
function page({ android = false, supported = true, startError = null } = {}) {
  const storage = new Map(), instances = [], nativeCalls = [], requests = [], timers = new Map();
  let now = 0, timerId = 0;
  function node(id = '') {
    const listeners = {}, classes = new Set();
    return { id, value: '', textContent: '', innerHTML: '', disabled: false, readOnly: false,
      dataset: {}, style: {}, scrollHeight: 1, attributes: {},
      classList: { add(...xs) { xs.forEach(x => classes.add(x)); },
        remove(...xs) { xs.forEach(x => classes.delete(x)); }, contains(x) { return classes.has(x); },
        toggle(x, on = !classes.has(x)) { on ? classes.add(x) : classes.delete(x); return on; } },
      setAttribute(k, v) { this.attributes[k] = String(v); }, getAttribute(k) { return this.attributes[k]; },
      addEventListener(k, f) { (listeners[k] ||= []).push(f); },
      async emit(k, e = {}) { for (const f of listeners[k] || []) await f(e); },
      async click() { if (!this.disabled) await this.emit('click'); },
      focus() {}, blur() {}, appendChild() {}, remove() {}, querySelectorAll() { return []; }
    };
  }
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], node(m[1])]));
  const document = Object.assign(node(), { hidden: false, activeElement: null, body: node(),
    getElementById(id) { return nodes[id] || null; }, createElement() { return node(); },
    querySelector() { return node(); }, querySelectorAll() { return []; } });
  const localStorage = { getItem(k) { return storage.get(k) ?? null; },
    setItem(k, v) { storage.set(k, String(v)); }, removeItem(k) { storage.delete(k); },
    key(i) { return [...storage.keys()][i] ?? null; }, get length() { return storage.size; } };
  const window = node();
  class Recognizer {
    constructor() { instances.push(this); }
    start() { if (startError) throw startError; }
    stop() { this.stopped = true; }
    abort() { this.aborted = true; if (this.onend) this.onend(); }
    results(parts) {
      this.onresult?.({ resultIndex: 0, results: parts.map(([text, final]) =>
        Object.assign([{ transcript: text, confidence: 0.8 }], { isFinal: final })) });
    }
  }
  if (supported) window.SpeechRecognition = Recognizer;
  if (android) window.BaguNative = { ...localStorage, keys() { return JSON.stringify([...storage.keys()]); },
    getAppInfo() { return '{}'; },
    startSpeech(id) { nativeCalls.push(['start', id]); },
    stopSpeech(id) { nativeCalls.push(['stop', id]); },
    cancelSpeech(id) { nativeCalls.push(['cancel', id]); } };
  const context = vm.createContext({ window, document, localStorage, console, URL, URLSearchParams,
    location: { search: android ? '?platform=android&token=test-token' : '' },
    crypto: require('node:crypto').webcrypto, TextDecoder, Uint8Array, Blob, navigator: {},
    fetch(url, options) { requests.push([url, options]); return new Promise(() => {}); },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { at: now + ms, fn }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() { return 1; }, clearInterval() {},
    alert() { throw new Error('Unexpected modal'); }, confirm() { return true; }
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  run('session = {session_id:"s_test", items:[{id:7}], pending:[{id:7,question:"测试题",category:"测试"}]}; renderQuiz();');
  return { nodes, window, document, storage, instances, nativeCalls, requests, run,
    async start() {
      assert.ok(nodes['btn-speech'], 'answer flow must expose a speech-input button');
      await nodes['btn-speech'].click();
    },
    async native(type, extra = {}, id = nativeCalls[0]?.[1]) {
      await window.emit('bagu-speech', { detail: { requestId: id, type, ...extra } });
    },
    tick(ms) {
      now += ms;
      for (const [id, entry] of [...timers]) if (entry.at <= now && timers.delete(id)) entry.fn();
    }
  };
}

test('desktop appends final transcripts exactly once, saves draft, never grades', async () => {
  const p = page(); p.nodes.ans.value = '已有答案';
  await p.start();
  const r = p.instances[0];
  assert.ok(r, 'must start browser recognition');
  assert.equal(r.lang, 'zh-CN');
  assert.equal(p.nodes.ans.readOnly, true);
  assert.equal(p.nodes['btn-submit'].disabled, true);
  r.onstart();
  r.results([['第一句', true], ['草稿片段', false]]);
  assert.equal(p.nodes.ans.value, '已有答案\n第一句');
  assert.match(p.nodes['speech-preview'].textContent, /草稿片段/);
  r.results([['第一句', true], ['第二句', true]]);
  r.results([['第一句', true], ['第二句', true]]);
  await p.nodes['btn-speech'].click();
  assert.equal(r.stopped, true);
  assert.equal(p.nodes['btn-submit'].disabled, true, 'wait for final event before grading');
  r.onend();
  assert.equal(p.nodes.ans.value, '已有答案\n第一句第二句');
  assert.equal(p.storage.get('bagu-draft:s_test:7'), '已有答案\n第一句第二句');
  assert.equal(p.nodes['btn-submit'].disabled, false);
  assert.equal(p.nodes.ans.readOnly, false);
  assert.equal(p.requests.filter(([, o]) => o?.method === 'POST').length, 0);
});

test('speech diagnostics correlate failures without copying speech or provider messages', async () => {
  const p = page(), events = [];
  p.window.baguDiagnostics = { id: () => 'w_' + 'a'.repeat(32), record: value => events.push({...value}) };
  p.nodes.ans.value = 'PRIVATE_ANSWER';
  await p.start();
  const r = p.instances[0];
  r.results([['PRIVATE_VOICE', true]]);
  r.onerror({error:'PRIVATE_KEY sk-test-provider-message'});
  assert.equal(events.length, 2);
  assert.deepEqual(events.map(e => e.event), ['web.speech', 'web.speech']);
  assert.deepEqual(events.map(e => e.stage), ['start', 'error']);
  assert.equal(events[0].operation_id, events[1].operation_id);
  assert.match(p.nodes['speech-error'].textContent, /w_a{32}/);
  assert.doesNotMatch(JSON.stringify(events), /PRIVATE_|sk-test/);
  assert.equal(p.nodes.ans.readOnly, false);
});

test('unsupported browser gives visible actionable error and preserves the answer', async () => {
  const p = page({ supported: false }); p.nodes.ans.value = '保留我'; await p.start();
  assert.match(p.nodes['speech-error'].textContent, /不支持|不可用/);
  assert.equal(p.nodes['speech-error'].classList.contains('hidden'), false);
  assert.equal(p.nodes.ans.value, '保留我');
  assert.equal(p.nodes['btn-submit'].disabled, false);
});

for (const code of ['not-allowed', 'service-not-allowed', 'audio-capture', 'network', 'no-speech', 'language-not-supported', 'aborted', 'unknown']) {
  test(`browser error ${code} releases controls and ignores late transcripts`, async () => {
    const p = page(); p.nodes.ans.value = '原文'; await p.start(); const r = p.instances[0];
    const late = r.onresult;
    r.onerror({ error: code, message: 'PRIVATE PROVIDER DETAIL' });
    assert.ok(p.nodes['speech-error'].textContent.length > 0);
    assert.doesNotMatch(p.nodes['speech-error'].textContent, /PRIVATE/);
    late({ resultIndex: 0, results: [Object.assign([{ transcript: '迟到' }], { isFinal: true })] });
    assert.equal(p.nodes.ans.value, '原文');
    assert.equal(p.nodes.ans.readOnly, false);
    assert.equal(p.nodes['btn-submit'].disabled, false);
    await p.start(); assert.equal(p.instances.length, 2, 'retry starts a new recognizer');
  });
}

test('constructor/start exceptions do not leave a recording UI', async () => {
  const p = page({ startError: new Error('private engine failure') }); await p.start();
  assert.ok(p.nodes['speech-error'].textContent);
  assert.equal(p.nodes.ans.readOnly, false);
  assert.equal(p.nodes['btn-submit'].disabled, false);
});

test('stop without result and startup silence time out without changing draft', async () => {
  for (const stop of [true, false]) {
    const p = page(); p.nodes.ans.value = '原文'; await p.start();
    if (stop) await p.nodes['btn-speech'].click();
    p.tick(150000);
    assert.match(p.nodes['speech-error'].textContent, /超时/);
    assert.equal(p.nodes.ans.value, '原文');
    assert.equal(p.nodes.ans.readOnly, false);
    assert.equal(p.instances[0].aborted, true);
  }
});

test('natural end with no final text is an explicit no-result error', async () => {
  const p = page(); await p.start(); p.instances[0].onend();
  assert.match(p.nodes['speech-error'].textContent, /未识别|没有识别/);
});

for (const action of ['cancel', 'navigate', 'render', 'pagehide', 'background', 'back', 'skip']) {
  test(`${action} cancels capture and late callbacks cannot fill a different question`, async () => {
    const p = page(); await p.start(); const r = p.instances[0], late = r.onresult;
    r.results([['未确认片段', false]]);
    if (action === 'cancel') await p.nodes['btn-speech-cancel'].click();
    if (action === 'navigate') p.run('showView("settings")');
    if (action === 'render') p.run('session.pending = [{id:8,question:"另一题",category:"测试"}]; renderQuiz()');
    if (action === 'pagehide') await p.window.emit('pagehide');
    if (action === 'background') { p.document.hidden = true; await p.document.emit('visibilitychange'); }
    if (action === 'back') assert.equal(p.run('window.baguHandleBack()'), true);
    if (action === 'skip') { p.nodes['btn-skip'].click(); }
    assert.equal(r.aborted, true);
    late({ resultIndex: 0, results: [Object.assign([{ transcript: '错误答案' }], { isFinal: true })] });
    assert.equal(p.nodes.ans.value, '');
    assert.equal(p.nodes.ans.readOnly, false);
  });
}

test('Android uses native bridge only and completes through request-scoped events', async () => {
  const p = page({ android: true }); p.nodes.ans.value = '原文'; await p.start();
  assert.equal(p.instances.length, 0);
  assert.equal(p.nativeCalls[0][0], 'start');
  assert.match(p.nativeCalls[0][1], /^[A-Za-z0-9_-]{1,80}$/);
  await p.native('partial', { text: '预览' }); assert.equal(p.nodes.ans.value, '原文');
  await p.nodes['btn-speech'].click(); assert.equal(p.nativeCalls[1][0], 'stop');
  await p.native('result', { text: '最终答案' }, 'other_request'); assert.equal(p.nodes.ans.value, '原文');
  await p.native('result', { text: '最终答案' });
  await p.native('result', { text: '最终答案' });
  assert.equal(p.nodes.ans.value, '原文\n最终答案');
  assert.equal(p.storage.get('bagu-draft:s_test:7'), '原文\n最终答案');
  assert.equal(p.nodes.ans.readOnly, false);
});

test('Android unavailable service and cancelled permission release the UI', async () => {
  const p = page({ android: true }); await p.start();
  await p.native('error', { message: '系统语音识别服务不可用，请使用输入法语音输入。' });
  assert.match(p.nodes['speech-error'].textContent, /不可用/);
  assert.equal(p.nodes['btn-submit'].disabled, false);
  await p.start();
  const secondId = p.nativeCalls.filter(x => x[0] === 'start')[1][1];
  await p.native('cancelled', {}, secondId);
  assert.equal(p.nodes.ans.readOnly, false);
});

test('Android permission dialog visibility does not swallow a later denial', async () => {
  const p = page({ android: true }); await p.start();
  p.document.hidden = true;
  await p.document.emit('visibilitychange');
  assert.equal(p.nativeCalls.filter(x => x[0] === 'cancel').length, 0,
    'native Activity owns Android pause; a permission dialog is not a JS cancel');
  p.tick(45000);
  assert.equal(p.nodes.ans.readOnly, true, 'allow time to read the native permission dialog');
  await p.native('error', { message: '未获得麦克风权限，请允许权限后重试。' });
  assert.match(p.nodes['speech-error'].textContent, /权限/);
  assert.equal(p.nodes.ans.readOnly, false);
  assert.equal(p.nodes['btn-submit'].disabled, false);
});

test('Android native pause terminal event releases recording controls', async () => {
  const p = page({ android: true }); await p.start(); await p.native('ready');
  p.document.hidden = true; await p.document.emit('visibilitychange');
  await p.native('cancelled');
  await p.native('result', { text: '后台迟到结果' });
  assert.equal(p.nodes.ans.value, ''); assert.equal(p.nodes.ans.readOnly, false);
});

test('old Android host reports unavailable instead of falling back to WebView recognition', async () => {
  const p = page({ android: true }); delete p.window.BaguNative.startSpeech; await p.start();
  assert.match(p.nodes['speech-error'].textContent, /不可用|更新/);
  assert.equal(p.instances.length, 0);
});

test('grading or revealing answer prevents a fresh recording', async () => {
  for (const state of ['grading', 'graded', 'memorize']) {
    const p = page();
    if (state === 'grading') p.nodes['btn-submit'].disabled = true;
    if (state === 'graded') p.nodes.ans.disabled = true;
    if (state === 'memorize') p.storage.set('bagu-session-mode:s_test', 'memorize');
    await p.start();
    assert.equal(p.instances.length, 0);
  }
});
