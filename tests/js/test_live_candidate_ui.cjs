/**
 * Regression tests for Live Set Overview and Live-derived Candidate UI in index.html.
 *
 * Requirements tested:
 * 1. Overview API Live Set info is truthfully displayed, from the flat reply or a legacy live_set object.
 * 2. Overview failures / connection errors are displayed as failures without misleading info.
 * 3. Candidate generation errors (empty brief, >4000 chars, non-local AI) are clearly displayed.
 * 4. Successful candidate generation distinguishes between Live-filled tempo and user-specified tempo.
 * 5. Candidate generation is explicitly marked as pre-approval, without touching Live.
 * 6. Stale success states are immediately replaced on subsequent attempts or errors.
 * 7. Key/genre/notes are not guessed or fabricated from tempo or track names.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('src/kihachi_mcp/studio/static/index.html', 'utf8');
assert.ok(html.includes('const refreshHealth = singleFlight('), 'Health polling must coalesce in-flight requests');

// Markup presence checks
assert.ok(html.includes('id="live-overview-load"'), 'live-overview-load button missing');
assert.ok(html.includes('id="live-overview"'), 'live-overview output missing');
assert.ok(html.includes('id="generate-from-live"'), 'generate-from-live button missing');
assert.ok(html.includes('id="live-candidate-context"'), 'live-candidate-context element missing');

function createMockEnv() {
  const nodes = {
    'live-overview-load': { disabled: false, onclick: null },
    'live-overview': { innerHTML: '', textContent: '' },
    'generate-from-live': { disabled: false, onclick: null },
    'live-candidate-context': { innerHTML: '', textContent: '' },
    'brief': { value: 'Dマイナー、8小節のテクノ' },
    'job-line': { textContent: '' },
    'generate': { disabled: false, onclick: null },
    'cancel': { disabled: true, onclick: null },
    'regenerate': { disabled: true, onclick: null },
    'preview-apply': { disabled: true, onclick: null },
    'apply': { disabled: true, onclick: null },
  };

  const $ = (id) => nodes[id] || { textContent: '', innerHTML: '', disabled: false };

  const apiCalls = [];
  let nextGetResponse = null;
  let nextPostResponse = null;

  const escapeHtml = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const getJson = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const payload = opts.body ? JSON.parse(opts.body) : null;
    apiCalls.push({ method, url, payload });
    if (method === 'GET') {
      if (nextGetResponse instanceof Error) throw nextGetResponse;
      if (typeof nextGetResponse === 'function') return nextGetResponse(url);
      return nextGetResponse || { ok: true };
    }
    if (nextPostResponse instanceof Error) throw nextPostResponse;
    if (typeof nextPostResponse === 'function') return nextPostResponse(url, payload);
    return nextPostResponse || { ok: true };
  };

  // Extract functions from html:
  // 1. live-overview-load
  const ovStart = html.indexOf("$('live-overview-load').onclick = async () => {");
  const ovEnd = html.indexOf('    function singleFlight(fn) {');
  assert.ok(ovStart > 0 && ovEnd > ovStart, 'Could not extract live-overview-load handler');

  // 2. generate-from-live
  const glStart = html.indexOf("$('generate-from-live').onclick = async () => {");
  const glEnd = html.indexOf('    $("cancel").onclick = async () => {');
  assert.ok(glStart > 0 && glEnd > glStart, 'Could not extract generate-from-live');

  const context = {
    $,
    escapeHtml,
    getJson,
    state: { poll: null },
    pollJob: () => {},
    window: { confirm: () => true },
    setInterval: () => 123,
    clearInterval: () => {},
  };

  vm.createContext(context);
  vm.runInContext(html.slice(ovStart, ovEnd), context);
  vm.runInContext(html.slice(glStart, glEnd), context);

  return {
    nodes,
    apiCalls,
    setNextGet(resp) { nextGetResponse = resp; },
    setNextPost(resp) { nextPostResponse = resp; },
  };
}

// -------------------------------------------------------------
// Test Suite
// -------------------------------------------------------------

console.log('--- 1. Live Set Overview (/api/live/overview) ---');

{
  const env = createMockEnv();
  env.setNextGet({
    ok: true,
    live_set: {
      tempo: 126,
      time_signature: '4/4',
      tracks: [
        { name: 'Drums', kind: 'midi', clip_count: 2 },
        { name: 'Bass & Lead', kind: 'audio', clip_count: 0 },
      ],
      scene_count: 4,
      cue_points: ['Intro', 'Break'],
    },
    live_changed: false,
  });

  const btn = env.nodes['live-overview-load'];
  const out = env.nodes['live-overview'];

  btn.onclick();
  assert.equal(btn.disabled, true, 'Button should be disabled during fetch');
  assert.ok(out.textContent.includes('読み取っています'), 'Loading state displayed');

  // Await microtasks
  setImmediate(async () => {
    assert.equal(btn.disabled, false, 'Button should be re-enabled after fetch');
    assert.equal(env.apiCalls.length, 1);
    assert.equal(env.apiCalls[0].url, '/api/live/overview');

    assert.ok(out.innerHTML.includes('126 BPM'), 'Tempo must be shown');
    assert.ok(out.innerHTML.includes('4/4拍子'), 'Meter must be shown');
    assert.ok(out.innerHTML.includes('トラック 2'), 'Track count must match');
    assert.ok(out.innerHTML.includes('シーン 4'), 'Scene count must match');
    assert.ok(out.innerHTML.includes('Drums'), 'Track name displayed');
    assert.ok(out.innerHTML.includes('Bass &amp; Lead'), 'Track name safely HTML escaped');
    assert.ok(out.innerHTML.includes('midi / クリップ: 2'), 'Kind and clip count displayed');
    assert.ok(out.innerHTML.includes('ノート数は取得していません'), 'Truthful note count notice');
    assert.ok(out.innerHTML.includes('Live は変更していません'), 'Truthful non-destructive notice');
    assert.ok(out.innerHTML.includes('キーは推定していません'), 'Key estimation prevented notice');
    console.log('  PASS: Successful overview renders accurate live_set facts');
  });
}

{
  // The flat shape GET /api/live/overview actually returns.
  const env = createMockEnv();
  env.setNextGet({
    ok: true,
    live_changed: false,
    set_name: 'KIHACHI_TEST016',
    tempo: 120.0,
    time_signature: { numerator: 4, denominator: 4 },
    tracks: [
      { index: 0, name: '1-Audio', track_type: 'audio', device_names: ['KIHACHI Live Device'] },
    ],
    scenes: [{ index: 0, name: '', color: '' }],
    note_counts_included: false,
  });

  const btn = env.nodes['live-overview-load'];
  const out = env.nodes['live-overview'];

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.innerHTML.includes('KIHACHI_TEST016'), 'Set name must be shown');
    assert.ok(out.innerHTML.includes('120 BPM'), 'Tempo must be shown');
    assert.ok(!out.innerHTML.includes('? BPM'), 'Tempo must not fall back to ?');
    assert.ok(out.innerHTML.includes('4/4拍子'), 'Meter object must render as 4/4');
    assert.ok(out.innerHTML.includes('トラック 1'), 'Track count must match');
    assert.ok(out.innerHTML.includes('シーン 1'), 'Scene count must come from scenes');
    assert.ok(out.innerHTML.includes('1-Audio（audio）'), 'Track name and track_type displayed');
    assert.ok(!out.innerHTML.includes('クリップ'), 'Clip count must not be invented when clips are not reported');
    console.log('  PASS: Flat overview reply renders set name, tempo, meter, tracks and scenes');
  });
}

{
  // Flat reply with clip lists: clips are counted per track, Live strings are escaped.
  const env = createMockEnv();
  env.setNextGet({
    ok: true,
    live_changed: false,
    set_name: '<img src=x onerror=alert(1)>',
    tempo: 124,
    time_signature: { numerator: 3, denominator: 4 },
    tracks: [
      { index: 0, name: 'Drums', track_type: 'midi' },
      { index: 1, name: 'Pad', track_type: 'midi' },
    ],
    scenes: [],
    session_clips: [{ track_index: 0, scene_index: 0, name: 'Beat' }],
    arrangement_clips: [{ track_index: 0, name: 'Beat A' }, { track_index: 1, name: 'Pad A' }],
  });

  const btn = env.nodes['live-overview-load'];
  const out = env.nodes['live-overview'];

  btn.onclick();
  setImmediate(() => {
    assert.ok(out.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'), 'Set name safely HTML escaped');
    assert.ok(!out.innerHTML.includes('<img'), 'Raw markup from Live must not reach the DOM');
    assert.ok(out.innerHTML.includes('3/4拍子'), 'Meter object rendered');
    assert.ok(out.innerHTML.includes('シーン 0'), 'Empty scene list counted');
    assert.ok(out.innerHTML.includes('Drums（midi / クリップ: 2）'), 'Session and Arrangement clips counted for Drums');
    assert.ok(out.innerHTML.includes('Pad（midi / クリップ: 1）'), 'Arrangement clip counted for Pad');
    console.log('  PASS: Flat overview reply escapes Live strings and counts clips per track');
  });
}

{
  const env = createMockEnv();
  env.setNextGet({ ok: false, error: 'Liveブリッジが起動していません' });

  const btn = env.nodes['live-overview-load'];
  const out = env.nodes['live-overview'];
  out.innerHTML = '<p>古い概要データ</p>';

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.equal(out.textContent, 'Liveブリッジが起動していません', 'Failure error rendered');
    assert.ok(!out.innerHTML.includes('古い概要データ'), 'Stale data cleared');
    console.log('  PASS: Overview failure clears stale output and displays error');
  });
}

{
  const env = createMockEnv();
  env.setNextGet(new Error('Network disconnected'));

  const btn = env.nodes['live-overview-load'];
  const out = env.nodes['live-overview'];

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.textContent.includes('接続できませんでした'), 'Network error handled');
    console.log('  PASS: Overview network exception handled safely');
  });
}

console.log('--- 2. Live-derived Candidate Generation (/api/live/candidate) ---');

{
  // Success with Live-derived tempo (brief omitted BPM)
  const env = createMockEnv();
  env.nodes['brief'].value = 'Dマイナー、8小節のテクノ';
  env.setNextPost({
    ok: true,
    live_context: {
      brief: '124 BPM。Dマイナー、8小節のテクノ',
      sources: { tempo: 'live' },
      tracks: ['Drums', 'Bass', 'Pad'],
      note_content_used: false,
    },
    live_changed: false,
  });

  const btn = env.nodes['generate-from-live'];
  const out = env.nodes['live-candidate-context'];

  btn.onclick();
  assert.equal(btn.disabled, true);
  assert.ok(out.textContent.includes('読み'), 'Loading message shown');

  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.equal(env.apiCalls.length, 1);
    assert.equal(env.apiCalls[0].url, '/api/live/candidate');
    assert.equal(env.apiCalls[0].payload.brief, 'Dマイナー、8小節のテクノ');

    assert.ok(out.textContent.includes('Liveから補完'), 'Tempo source distinguished as Live');
    assert.ok(out.textContent.includes('参照トラック数: 3'), 'Track count truthfully shown');
    assert.ok(out.textContent.includes('ノート内容参照: なし'), 'Note content unused verified');
    assert.ok(out.textContent.includes('キー・ジャンルは推定していません'), 'No key/genre estimation');
    assert.ok(out.textContent.includes('承認前の候補作成であり'), 'Marked as pre-approval');
    assert.ok(out.textContent.includes('Liveへの書き込み・保存・再生は行っていません'), 'Guaranteed untouched Live');
    console.log('  PASS: Live-completed tempo generation displays truthful sources and non-destructive status');
  });
}

{
  // Success with user-specified tempo (brief contained BPM)
  const env = createMockEnv();
  env.nodes['brief'].value = '130 BPM、D minor、4小節ファンク';
  env.setNextPost({
    ok: true,
    live_context: {
      brief: '130 BPM、D minor、4小節ファンク',
      sources: { tempo: 'user' },
      tracks: ['Master'],
      note_content_used: false,
    },
    live_changed: false,
  });

  const btn = env.nodes['generate-from-live'];
  const out = env.nodes['live-candidate-context'];

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.textContent.includes('制作指示（ユーザー指定）'), 'User tempo respected and distinguished');
    assert.ok(!out.textContent.includes('Liveから補完'), 'Must not claim Live filled tempo');
    console.log('  PASS: User-specified tempo is recognized and distinguished from Live tempo');
  });
}

{
  // Non-local AI provider rejection
  const env = createMockEnv();
  env.setNextPost({
    ok: false,
    error: 'Liveからの候補生成はローカルAI設定で実行してください',
  });

  const btn = env.nodes['generate-from-live'];
  const out = env.nodes['live-candidate-context'];
  out.textContent = '以前の成功表示';

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.textContent.includes('ローカルAI設定で実行してください'), 'Rejection error message shown');
    assert.ok(out.textContent.includes('Liveへの書き込み・保存・再生は行っていません'), 'Safe status confirmed');
    assert.ok(!out.textContent.includes('以前の成功表示'), 'Stale text overwritten');
    console.log('  PASS: OpenAI provider rejection error is clearly displayed and clears stale state');
  });
}

{
  // Empty or invalid brief rejection
  const env = createMockEnv();
  env.nodes['brief'].value = '   ';
  env.setNextPost({
    ok: false,
    error: 'briefは1〜4000文字で指定してください',
  });

  const btn = env.nodes['generate-from-live'];
  const out = env.nodes['live-candidate-context'];

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.textContent.includes('1〜4000文字で指定してください'), 'Validation error shown');
    assert.ok(out.textContent.includes('Liveへの書き込み・保存・再生は行っていません'), 'Safe status confirmed');
    console.log('  PASS: Invalid brief rejection error is clearly displayed');
  });
}

{
  // Network exception on candidate generation
  const env = createMockEnv();
  env.setNextPost(new Error('Connection refused'));

  const btn = env.nodes['generate-from-live'];
  const out = env.nodes['live-candidate-context'];

  btn.onclick();
  setImmediate(() => {
    assert.equal(btn.disabled, false);
    assert.ok(out.textContent.includes('接続できませんでした'), 'Connection error displayed');
    assert.ok(out.textContent.includes('候補は生成していません'), 'Non-generation confirmed');
    console.log('  PASS: Network error on candidate generation handled safely');
  });
}

setTimeout(() => {
  console.log('\nAll Live candidate UI regression tests passed successfully!');
}, 50);

{
  const start = html.indexOf('    function singleFlight(fn) {');
  const end = html.indexOf('    const refreshHealth = singleFlight', start);
  assert.ok(start >= 0 && end > start, 'Could not extract singleFlight helper');
  const context = {};
  vm.createContext(context);
  vm.runInContext(html.slice(start, end), context);

  let calls = 0;
  let release;
  const wrapped = context.singleFlight(() => {
    calls += 1;
    if (calls > 1) return Promise.resolve('again');
    return new Promise((resolve) => { release = resolve; });
  });
  const first = wrapped('first');
  const second = wrapped('second');

  setImmediate(async () => {
    assert.equal(calls, 1, 'Concurrent health polls must share one request');
    release('done');
    assert.equal(await first, 'done');
    assert.equal(await second, 'done');
    assert.equal(await wrapped('third'), 'again');
    assert.equal(calls, 2, 'A new request may start after the first completes');
    console.log('  PASS: Concurrent health polls share one in-flight request');
  });
}
