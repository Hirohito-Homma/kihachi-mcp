/**
 * Arrangement preview, expansion result and verification rendering in index.html.
 *
 * The handlers are cut out of the real page and run against a minimal DOM:
 * 1. The independent readback status, error and every mismatch are shown.
 * 2. Names that come from Live (clip, locator, Set names) are escaped.
 * 3. A FAIL Arrangement line and VERIFICATION FAILED are rendered as failures.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('src/kihachi_mcp/studio/static/index.html', 'utf8');

function slice(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  const end = html.indexOf(endMarker, start);
  assert.ok(start > 0 && end > start, `Could not extract ${startMarker}`);
  return html.slice(start, end);
}

function createEnv() {
  const nodes = {};
  const $ = (id) => (nodes[id] ||= { innerHTML: '', textContent: '', disabled: false, onclick: null });
  let nextResponse = null;
  const calls = [];
  const context = {
    $,
    state: { candidate: { candidate_id: 'cand0001' }, applying: false },
    getJson: async (url, options = {}) => {
      calls.push({ url, body: options.body ? JSON.parse(options.body) : null });
      return nextResponse;
    },
  };
  vm.createContext(context);
  vm.runInContext(slice('const esc = ', '\n'), context);
  vm.runInContext(slice('$("preview-arrange").onclick = async () => {', '    function escapeHtml(text) {'), context);
  vm.runInContext(slice('function renderVerification(result) {', '    $("recheck-coverage").onclick'), context);
  return { nodes, $, calls, context, respond(value) { nextResponse = value; } };
}

const hostile = '<img src=x onerror=alert(1)>';

(async () => {
  {
    const env = createEnv();
    env.respond({
      ok: false,
      status: 'blocked',
      summary: { set_name: hostile, clip_count: 3, bars: 32, locators: [`Drop ${hostile}`] },
      conflicts: [{ detail: `'${hostile}' があります。既存のクリップは動かしません` }],
      warnings: [hostile],
    });
    await env.$('preview-arrange').onclick();
    const out = env.$('arrange-summary').innerHTML;
    assert.ok(!out.includes('<img'), 'Live-provided names must not become markup');
    assert.ok(out.includes('&lt;img src=x onerror=alert(1)&gt;'), 'escaped name is still shown');
    assert.equal(env.$('arrange').disabled, true, 'a blocked preview keeps expansion disabled');
  }

  {
    const env = createEnv();
    env.respond({
      ok: false,
      execution_status: 'verified',
      partial: false,
      receipt: {
        status: 'verification_failed',
        error: 'Live did not report locators, so the created locators are unconfirmed',
        completed_operations: ['001', '002'],
        attempted_operations: ['001', '002'],
        mismatches: [
          { operation_id: '002-create_locator', field_name: 'name', expected: 'Drop [KIHACHI]', observed: hostile },
          { operation_id: '001-place_arrangement_clip', field_name: 'length_beats', expected: 64, observed: 60 },
        ],
      },
      verified_means: 'Liveが計画どおりクリップとロケーターを置いたこと。',
    });
    await env.$('arrange').onclick();
    const out = env.$('arrange-result').innerHTML;
    assert.ok(out.includes('<b>verification_failed</b>'), 'second readback status is shown');
    assert.ok(out.includes('did not report locators'), 'receipt error is shown');
    assert.ok(out.includes('002-create_locator name: 計画 &quot;Drop [KIHACHI]&quot;'), 'locator mismatch is listed');
    assert.ok(out.includes('001-place_arrangement_clip length_beats: 計画 64 / Live 60'), 'clip mismatch is listed');
    assert.ok(!out.includes('<img'), 'observed Live names are escaped');
    assert.equal(env.context.state.applying, false);
    assert.deepEqual(env.calls[0], { url: '/api/arrangement', body: { candidate_id: 'cand0001', confirmed: true } });
  }

  {
    const env = createEnv();
    env.respond({
      ok: false,
      error: '展開記録を保存できませんでした（disk full）。',
      receipt: { status: 'verified', completed_operations: ['001'], attempted_operations: ['001'], mismatches: [] },
    });
    await env.$('arrange').onclick();
    const out = env.$('arrange-result').innerHTML;
    assert.ok(out.includes('<b>verified</b>'));
    assert.ok(out.includes('<span class="bad">展開記録を保存できませんでした'), 'a record failure is shown as a failure');
    assert.ok(!out.includes('<li'), 'no mismatches are invented');
  }

  {
    const env = createEnv();
    env.context.renderVerification({
      ok: false,
      status: 'VERIFICATION FAILED',
      lines: [
        { name: 'Tempo', status: 'PASS', detail: '120 BPM' },
        { name: 'Arrangement', status: 'FAIL', detail: `未確認: Liveがロケーター一覧を返しません; 003 name: 計画 X / Live ${hostile}` },
      ],
      verified_means: 'Liveから読み戻した値が計画と一致したこと。',
    });
    const out = env.$('verify-result').innerHTML;
    assert.ok(out.includes('<td>Arrangement</td><td class="bad"><b>FAIL</b></td>'), 'FAIL line is marked bad');
    assert.ok(out.includes('<td class="ok"><b>PASS</b></td>'));
    assert.ok(out.includes('未確認: Liveがロケーター一覧を返しません'));
    assert.ok(!out.includes('<img'), 'line details are escaped');
    assert.ok(out.includes('<b class="bad">VERIFICATION FAILED</b>'), 'overall failure is marked bad');
  }

  console.log('arrangement UI tests passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
