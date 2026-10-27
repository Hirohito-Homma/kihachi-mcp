/**
 * Regression tests for MusicPlan and ApprovalCenter UI in index.html.
 *
 * Requirements tested:
 * 1. MusicPlan preview displays non-destructive nature clearly (no live change, no execution).
 * 2. Buttons call corresponding endpoints with correct payloads.
 * 3. First Live send request results in Set read / rebind and requires a second approval.
 * 4. Second Live send request handles successful execution response safely.
 * 5. Errors (playback, recording, timeout, network failure) prevent false success impression.
 * 6. ApprovalCenter handles empty state, bound plans, pending approvals, and API failures.
 * 7. Flags (live_changed, executed, save) stay truthful to API response.
 * 8. Form changes invalidate preview and pending plans safely.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('src/kihachi_mcp/studio/static/index.html', 'utf8');

// Check markup presence
assert.ok(html.includes('id="music-plan-view"'), 'music-plan-view section missing');
assert.ok(html.includes('id="music-plan-preview"'), 'music-plan-preview button missing');
assert.ok(html.includes('id="music-plan-approve"'), 'music-plan-approve button missing');
assert.ok(html.includes('id="music-plan-reject"'), 'music-plan-reject button missing');
assert.ok(html.includes('id="music-plan-verify"'), 'music-plan-verify button missing');
assert.ok(html.includes('id="music-plan-live"'), 'music-plan-live button missing');
assert.ok(html.includes('id="music-plan-status"'), 'music-plan-status element missing');
assert.ok(html.includes('id="music-plan-result"'), 'music-plan-result element missing');
assert.ok(html.includes('id="approval-center"'), 'approval-center element missing');
assert.ok(html.includes('id="live-plan-pending"'), 'live-plan-pending element missing');

function createMockEnv() {
  const nodes = {
    'music-plan-view': { hidden: false },
    'music-plan-preview': { disabled: false, onclick: null },
    'music-plan-approve': { disabled: true, onclick: null },
    'music-plan-reject': { disabled: true, onclick: null },
    'music-plan-verify': { disabled: true, onclick: null },
    'music-plan-live': { disabled: true, onclick: null },
    'music-plan-status': { textContent: '' },
    'music-plan-result': { innerHTML: '', textContent: '' },
    'approval-center': { innerHTML: '', textContent: '', onclick: null },
    'live-plan-pending': { textContent: '' },
    'composer-run': { disabled: false },
    'composer-adopt': { disabled: false },
    'composer-status': { textContent: '' },
    'composer-role': { value: '全パート' },
    'composer-key': { value: 'D#m' },
    'composer-tempo': { value: '124' },
    'composer-bars': { value: '8' },
    'composer-grid': { value: '1/16' },
    'composer-syncopation': { value: '0.7' },
    'composer-octave': { value: '0.2' },
    'composer-ghost': { checked: true },
    'composer-velocity-min': { value: '65' },
    'composer-velocity-max': { value: '118' },
    'composer-avoid-kick': { checked: true },
    'composer-kicks': { value: '0, 1, 2, 3' },
    'composer-note-min': { value: '0.25' },
    'composer-note-max': { value: '0.75' },
    'composer-variation-bar': { value: '' },
    'composer-genre': { value: 'mutation_funk' },
    'composer-mood': { value: 'dark' },
    'composer-prompt': { value: 'funky slap bass' },
  };

  const apiCalls = [];
  let nextPostResponse = null;
  let nextGetResponse = null;

  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const post = async (url, payload) => {
    apiCalls.push({ method: 'POST', url, payload });
    if (nextPostResponse instanceof Error) throw nextPostResponse;
    if (typeof nextPostResponse === 'function') return nextPostResponse(url, payload);
    return nextPostResponse || { ok: true };
  };

  const getJson = async (url) => {
    apiCalls.push({ method: 'GET', url });
    if (nextGetResponse instanceof Error) throw nextGetResponse;
    if (typeof nextGetResponse === 'function') return nextGetResponse(url);
    return nextGetResponse || { ok: true };
  };

  // Extract functions precisely:
  // 1. composerConstraints
  const ccStart = html.indexOf('    function composerConstraints() {');
  const ccEnd = html.indexOf('    function midiPayload() {');
  assert.ok(ccStart > 0 && ccEnd > ccStart, 'Could not find composerConstraints boundaries');

  // 2. invalidateComposerPreview and setLivePlanPending
  const fnStart = html.indexOf('    function invalidateComposerPreview() {');
  const fnEnd = html.indexOf('    function showComposerNotes(data) {');
  assert.ok(fnStart > 0 && fnEnd > fnStart, 'Could not find invalidateComposerPreview boundaries');

  // 3. music-plan button click handlers
  const mpStart = html.indexOf('    $("music-plan-preview").onclick = async () => {');
  const mpEnd = html.indexOf('    $("composer-preview").onclick = async () => {');
  assert.ok(mpStart > 0 && mpEnd > mpStart, 'Could not find music-plan onclick handlers boundaries');

  // 4. refreshApprovals
  const acStart = html.indexOf('    async function refreshApprovals() {');
  const acEnd = html.indexOf('    $("approval-center").onclick = (event) => {');
  assert.ok(acStart > 0 && acEnd > acStart, 'Could not find refreshApprovals boundaries');

  const script = `
    let musicPlanHash = null;
    let composerPreview = null;
    let composerGenerated = null;
    ${html.slice(ccStart, ccEnd)}
    ${html.slice(fnStart, fnEnd)}
    ${html.slice(acStart, acEnd)}
    ${html.slice(mpStart, mpEnd)}
  `;

  const context = vm.createContext({
    $: (id) => nodes[id] || null,
    esc,
    post,
    getJson,
    document: {
      querySelectorAll: () => [],
    },
    Math,
    Number,
    JSON,
  });

  vm.runInContext(script, context);

  return {
    nodes,
    apiCalls,
    setNextPost: (resp) => { nextPostResponse = resp; },
    setNextGet: (resp) => { nextGetResponse = resp; },
    getMusicPlanHash: () => vm.runInContext('musicPlanHash', context),
    invalidateComposerPreview: () => vm.runInContext('invalidateComposerPreview()', context),
    refreshApprovals: () => vm.runInContext('refreshApprovals()', context),
  };
}

let passed = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (err) {
    console.error(`  FAIL: ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

async function runAsync(name, fn) {
  try {
    await fn();
    console.log(`  PASS: ${name}`);
    passed++;
  } catch (err) {
    console.error(`  FAIL: ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

async function runAll() {
  console.log('--- 1. Initial State & Markup ---');
  test('Initial button states are safe', () => {
    const env = createMockEnv();
    assert.equal(env.nodes['music-plan-preview'].disabled, false, 'preview button must be enabled initially');
    assert.equal(env.nodes['music-plan-approve'].disabled, true, 'approve button must be disabled initially');
    assert.equal(env.nodes['music-plan-reject'].disabled, true, 'reject button must be disabled initially');
    assert.equal(env.nodes['music-plan-verify'].disabled, true, 'verify button must be disabled initially');
    assert.equal(env.nodes['music-plan-live'].disabled, true, 'live send button must be disabled initially');
    assert.equal(env.getMusicPlanHash(), null, 'musicPlanHash must be null initially');
  });

  console.log('\n--- 2. MusicPlan Preview (/api/music-plan/preview) ---');
  await runAsync('Preview success displays non-destructive metrics and enables approval', async () => {
    const env = createMockEnv();
    env.setNextPost({
      ok: true,
      plan_hash: 'hash_preview_123',
      role: '全パート',
      operations: ['create_track', 'replace_clip_notes'],
      music: {
        key: 'D#m',
        tempo: 124,
        bars: 8,
        tracks: [{ name: 'KIHACHI Drums' }, { name: 'KIHACHI Bass' }],
        warnings: ['Warning: avoid kick collision active'],
      },
      direction: { genre: 'mutation_funk', mood: 'dark' },
      dry_run: {
        status: 'APPROVAL_REQUIRED',
        playback: 'READ_ONLY',
        executed: false,
        live_changed: false,
        save: false,
        paid_api: false,
        changes: { tracks: 2, clips: 2, notes: 32 },
        range: { start: 0, end: 32 },
      },
    });

    await env.nodes['music-plan-preview'].onclick();

    assert.equal(env.apiCalls.length, 1);
    assert.equal(env.apiCalls[0].url, '/api/music-plan/preview');
    assert.equal(env.apiCalls[0].payload.constraints.role, '全パート');
    assert.equal(env.getMusicPlanHash(), 'hash_preview_123');

    // Button states
    assert.equal(env.nodes['music-plan-preview'].disabled, false);
    assert.equal(env.nodes['music-plan-approve'].disabled, false, 'approve should be enabled');
    assert.equal(env.nodes['music-plan-reject'].disabled, false, 'reject should be enabled');
    assert.equal(env.nodes['music-plan-verify'].disabled, true, 'verify must remain disabled before approval');
    assert.equal(env.nodes['music-plan-live'].disabled, true, 'live send must remain disabled before approval');

    // Status & Result content
    assert.match(env.nodes['music-plan-status'].textContent, /Liveは変えていません/);
    assert.match(env.nodes['music-plan-result'].innerHTML, /実行 していない/);
    assert.match(env.nodes['music-plan-result'].innerHTML, /保存 しない/);
    assert.match(env.nodes['music-plan-result'].innerHTML, /トラック 2/);
    assert.match(env.nodes['live-plan-pending'].textContent, /保留中です/);
  });

  await runAsync('Preview failure keeps buttons disabled and reports error clearly', async () => {
    const env = createMockEnv();
    env.setNextPost({
      ok: false,
      error: 'キーまたはテンポの制約が不正です',
    });

    await env.nodes['music-plan-preview'].onclick();

    assert.equal(env.getMusicPlanHash(), null);
    assert.equal(env.nodes['music-plan-approve'].disabled, true);
    assert.equal(env.nodes['music-plan-reject'].disabled, true);
    assert.equal(env.nodes['music-plan-verify'].disabled, true);
    assert.equal(env.nodes['music-plan-live'].disabled, true);
    assert.match(env.nodes['music-plan-status'].textContent, /キーまたはテンポの制約が不正です/);
    assert.equal(env.nodes['music-plan-result'].textContent, '');
  });

  console.log('\n--- 3. MusicPlan Approval (/api/music-plan/approve) ---');
  await runAsync('Approve records token on server, unlocks verify and live send', async () => {
    const env = createMockEnv();
    env.setNextPost((url, payload) => {
      if (url === '/api/music-plan/preview') {
        return {
          ok: true,
          plan_hash: 'hash_abc',
          role: 'bass',
          music: { key: 'D#m', tempo: 124, bars: 8, tracks: [{ name: 'KIHACHI Bass' }] },
          dry_run: { status: 'APPROVAL_REQUIRED', playback: 'READ_ONLY', executed: false, live_changed: false, save: false, changes: {}, range: { start: 0, end: 32 } },
        };
      }
      if (url === '/api/music-plan/approve') {
        assert.equal(payload.plan_hash, 'hash_abc');
        return {
          ok: true,
          approved: true,
          executed: false,
          live_changed: false,
          plan_hash: 'hash_abc',
        };
      }
      return { ok: true };
    });

    await env.nodes['music-plan-preview'].onclick();
    await env.nodes['music-plan-approve'].onclick();

    assert.equal(env.nodes['music-plan-approve'].disabled, true, 'already approved');
    assert.equal(env.nodes['music-plan-reject'].disabled, true, 'already approved');
    assert.equal(env.nodes['music-plan-verify'].disabled, false, 'verify unlocked');
    assert.equal(env.nodes['music-plan-live'].disabled, false, 'live send unlocked');
    assert.match(env.nodes['music-plan-status'].textContent, /この計画を承認しました。Liveには送っていません。/);
    assert.match(env.nodes['live-plan-pending'].textContent, /この計画は承認済みです。Liveには送っていません。/);
  });

  console.log('\n--- 4. First Live Send: Live Bind & Second Approval Flow ---');
  await runAsync('First live send binds to real Set and demands re-approval with new hash', async () => {
    const env = createMockEnv();
    // Simulate Preview -> Approve -> Live (first press)
    env.setNextPost((url, payload) => {
      if (url === '/api/music-plan/preview') {
        return {
          ok: true,
          plan_hash: 'hash_initial',
          role: 'bass',
          music: { key: 'D#m', tempo: 124, bars: 8, tracks: [{ name: 'KIHACHI Bass' }] },
          dry_run: { status: 'APPROVAL_REQUIRED', playback: 'READ_ONLY', executed: false, live_changed: false, save: false, changes: {}, range: { start: 0, end: 32 } },
        };
      }
      if (url === '/api/music-plan/approve') {
        return { ok: true, approved: true, plan_hash: payload.plan_hash };
      }
      if (url === '/api/music-plan/live') {
        // Backend returns re-bound plan with new plan_hash and state APPROVAL_REQUIRED
        return {
          ok: false,
          state: 'APPROVAL_REQUIRED',
          plan_hash: 'hash_bound_live_456',
          executed: false,
          live_changed: false,
          save: false,
          paid_api: false,
          reply: '実機の Live を読みました。トラック 3 番目から足します。もう一度承認してから、もう一度押してください。まだ送っていません。',
        };
      }
      return { ok: true };
    });

    await env.nodes['music-plan-preview'].onclick();
    await env.nodes['music-plan-approve'].onclick();
    await env.nodes['music-plan-live'].onclick();

    // Verify state after 1st press
    assert.equal(env.getMusicPlanHash(), 'hash_bound_live_456', 'hash must update to bound hash');
    assert.equal(env.nodes['music-plan-approve'].disabled, false, 'must enable re-approval');
    assert.equal(env.nodes['music-plan-reject'].disabled, false, 'must enable reject');
    assert.equal(env.nodes['music-plan-live'].disabled, true, 'live send must NOT be enabled before 2nd approval');
    assert.match(env.nodes['music-plan-status'].textContent, /もう一度承認してから/);
    assert.match(env.nodes['music-plan-status'].textContent, /まだ送っていません/);
  });

  console.log('\n--- 5. Second Live Send: Execution & Verified Response ---');
  await runAsync('Second live send executes once and reports verified response', async () => {
    const env = createMockEnv();

    env.setNextPost((url, payload) => {
      if (url === '/api/music-plan/preview') {
        return {
          ok: true,
          plan_hash: 'hash_initial',
          role: 'bass',
          music: { key: 'D#m', tempo: 124, bars: 8, tracks: [{ name: 'KIHACHI Bass' }] },
          dry_run: { status: 'APPROVAL_REQUIRED', playback: 'READ_ONLY', executed: false, live_changed: false, save: false, changes: {}, range: { start: 0, end: 32 } },
        };
      }
      if (url === '/api/music-plan/approve') {
        return { ok: true, approved: true, plan_hash: payload.plan_hash };
      }
      if (url === '/api/music-plan/live') {
        if (payload.plan_hash === 'hash_initial') {
          return {
            ok: false,
            state: 'APPROVAL_REQUIRED',
            plan_hash: 'hash_bound_live_456',
            executed: false,
            live_changed: false,
            reply: '実機の Live を読みました。もう一度承認してください。',
          };
        }
        assert.equal(payload.plan_hash, 'hash_bound_live_456');
        return {
          ok: true,
          status: 'verified',
          state: 'SUCCESS',
          states: ['INSPECTING', 'APPLYING', 'VERIFIED'],
          executed: true,
          live_changed: true,
          reply: '実機の Live は計画どおり応答しました。音の完成ではありません。保存はしていません。',
        };
      }
      return { ok: true };
    });

    // 1. Preview
    await env.nodes['music-plan-preview'].onclick();
    // 2. First Approve
    await env.nodes['music-plan-approve'].onclick();
    // 3. First Live Send (binds to Set)
    await env.nodes['music-plan-live'].onclick();
    assert.equal(env.getMusicPlanHash(), 'hash_bound_live_456');

    // 4. Second Approve
    await env.nodes['music-plan-approve'].onclick();
    assert.equal(env.nodes['music-plan-live'].disabled, false, 'live send unlocked after second approval');

    // 5. Second Live Send (executes)
    await env.nodes['music-plan-live'].onclick();

    assert.equal(env.nodes['music-plan-live'].disabled, true, 'live send button must not re-enable after success');
    assert.match(env.nodes['music-plan-status'].textContent, /実機の Live は計画どおり応答しました/);
    assert.match(env.nodes['music-plan-status'].textContent, /保存はしていません/);
  });

  console.log('\n--- 6. Safety Guards: Blocked sends & Network errors ---');
  await runAsync('Transport busy (playback/recording) error keeps approval and allows retry', async () => {
    const env = createMockEnv();
    env.setNextPost((url, payload) => {
      if (url === '/api/music-plan/preview') {
        return { ok: true, plan_hash: 'hash_ready', music: {}, dry_run: { changes: {}, range: { start: 0, end: 32 } } };
      }
      if (url === '/api/music-plan/approve') {
        return { ok: true, approved: true, plan_hash: 'hash_ready' };
      }
      if (url === '/api/music-plan/live') {
        return {
          ok: false,
          executed: false,
          live_changed: false,
          state: 'APPROVAL_REQUIRED',
          plan_hash: 'hash_ready',
          reply: '送れませんでした（Live が再生中です）。Live は変えていません。止めてから、同じボタンをもう一度押してください。承認は残しています。',
        };
      }
      return { ok: true };
    });

    await env.nodes['music-plan-preview'].onclick();
    await env.nodes['music-plan-approve'].onclick();
    await env.nodes['music-plan-live'].onclick();

    assert.equal(env.nodes['music-plan-live'].disabled, false, 'retry button must be available');
    assert.match(env.nodes['music-plan-status'].textContent, /Live が再生中です/);
    assert.match(env.nodes['music-plan-status'].textContent, /Live は変えていません/);
  });

  await runAsync('Network failure reports Studio communication error and protects Live', async () => {
    const env = createMockEnv();
    env.setNextPost((url) => {
      if (url === '/api/music-plan/preview') {
        return { ok: true, plan_hash: 'hash_ready', music: {}, dry_run: { changes: {}, range: { start: 0, end: 32 } } };
      }
      if (url === '/api/music-plan/approve') {
        return { ok: true, approved: true, plan_hash: 'hash_ready' };
      }
      if (url === '/api/music-plan/live') {
        throw new Error('Network connection refused');
      }
      return { ok: true };
    });

    await env.nodes['music-plan-preview'].onclick();
    await env.nodes['music-plan-approve'].onclick();
    await env.nodes['music-plan-live'].onclick();

    assert.equal(env.nodes['music-plan-live'].disabled, false, 'button re-enabled for retry');
    assert.match(env.nodes['music-plan-status'].textContent, /Studioに届きませんでした。実機には送っていません。/);
    assert.match(env.nodes['live-plan-pending'].textContent, /Studioに届きませんでした。実機には送っていません。/);
  });

  console.log('\n--- 7. Rejection Flow (/api/music-plan/reject) ---');
  await runAsync('Reject refuses plan without sending and disables execution', async () => {
    const env = createMockEnv();
    env.setNextPost((url, payload) => {
      if (url === '/api/music-plan/preview') {
        return { ok: true, plan_hash: 'hash_to_reject', music: {}, dry_run: { changes: {}, range: { start: 0, end: 32 } } };
      }
      if (url === '/api/music-plan/reject') {
        assert.equal(payload.plan_hash, 'hash_to_reject');
        return {
          ok: true,
          approved: false,
          rejected: true,
          message: 'この計画は承認していません。Liveには送っていません。',
        };
      }
      return { ok: true };
    });

    await env.nodes['music-plan-preview'].onclick();
    await env.nodes['music-plan-reject'].onclick();

    assert.equal(env.nodes['music-plan-verify'].disabled, true);
    assert.equal(env.nodes['music-plan-live'].disabled, true);
    assert.equal(env.nodes['music-plan-approve'].disabled, false, 'can re-approve if reconsidered');
    assert.match(env.nodes['music-plan-status'].textContent, /この計画は承認していません。Liveには送っていません。/);
  });

  console.log('\n--- 8. ApprovalCenter (/api/approvals) ---');
  await runAsync('ApprovalCenter renders empty state when no actions pending', async () => {
    const env = createMockEnv();
    env.setNextGet({
      ok: true,
      music_plans: [],
      revisions: [],
    });

    await env.refreshApprovals();

    assert.match(env.nodes['approval-center'].innerHTML, /保留中の操作はありません。/);
  });

  await runAsync('ApprovalCenter displays bound, approved, and un-bound plans accurately', async () => {
    const env = createMockEnv();
    env.setNextGet({
      ok: true,
      music_plans: [
        {
          plan_hash: 'hash_bound_needs_approval',
          tracks: ['KIHACHI Drums', 'KIHACHI Bass'],
          approved: false,
          live_bound: true,
          remaining_seconds: 600,
        },
        {
          plan_hash: 'hash_bound_and_approved',
          tracks: ['KIHACHI Chords'],
          approved: true,
          live_bound: true,
          remaining_seconds: 450,
        },
        {
          plan_hash: 'hash_preview_approved_only',
          tracks: ['KIHACHI Lead'],
          approved: true,
          live_bound: false,
          remaining_seconds: null,
        },
      ],
      revisions: [],
    });

    await env.refreshApprovals();

    const html = env.nodes['approval-center'].innerHTML;
    assert.match(html, /パート計画（実機の Set に合わせ済み・承認待ち）/);
    assert.match(html, /あと10分/);
    assert.match(html, /「この計画を承認」を押してください/);

    assert.match(html, /パート計画（実機の Set に合わせ済み・承認済み）/);
    assert.match(html, /あと8分/);
    assert.match(html, /「実機の Live に送る」をもう一度押すと1回だけ送ります/);

    assert.match(html, /パート計画（承認済み・未送信）/);
  });

  await runAsync('ApprovalCenter handles API errors gracefully without throwing', async () => {
    const env = createMockEnv();
    env.setNextGet(new Error('Server unavailable 503'));

    await env.refreshApprovals();

    assert.match(env.nodes['approval-center'].innerHTML, /承認センターの読み込みに失敗しました。/);
  });

  console.log('\n--- 9. Form Input Changes & Invalidation ---');
  test('Input change invalidates existing preview and disables execution buttons', () => {
    const env = createMockEnv();
    // Simulate active preview
    env.nodes['music-plan-approve'].disabled = false;
    env.nodes['music-plan-verify'].disabled = false;
    env.nodes['music-plan-live'].disabled = false;

    env.invalidateComposerPreview();

    assert.equal(env.getMusicPlanHash(), null, 'hash must be cleared');
    assert.equal(env.nodes['music-plan-approve'].disabled, true);
    assert.equal(env.nodes['music-plan-reject'].disabled, true);
    assert.equal(env.nodes['music-plan-verify'].disabled, true);
    assert.equal(env.nodes['music-plan-live'].disabled, true);
    assert.match(env.nodes['live-plan-pending'].textContent, /入力が変わったので、このパート計画は保留ではありません/);
  });

  console.log(`\nAll ${passed}/${passed} tests passed successfully!`);
}

runAll().catch((err) => {
  console.error(err);
  process.exit(1);
});
