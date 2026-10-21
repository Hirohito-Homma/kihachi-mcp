const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const INDEX_HTML_PATH = path.resolve(
  __dirname,
  '../../src/kihachi_mcp/studio/static/index.html',
);

const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');

function createNode(id, overrides = {}) {
  const listeners = {};
  return {
    id,
    value: '',
    checked: false,
    disabled: false,
    textContent: '',
    innerHTML: '',
    className: '',
    dataset: {},
    onclick: null,
    addEventListener(type, fn) {
      if (!listeners[type]) listeners[type] = [];
      listeners[type].push(fn);
    },
    dispatchEvent(event) {
      const fns = listeners[event.type] || [];
      for (const fn of fns) fn(event);
    },
    ...overrides,
  };
}

function createHarness() {
  const nodes = {
    // Composer controls
    'composer-role': createNode('composer-role', { value: 'bass' }),
    'composer-key': createNode('composer-key', { value: 'D#m' }),
    'composer-tempo': createNode('composer-tempo', { value: '124' }),
    'composer-bars': createNode('composer-bars', { value: '8' }),
    'composer-grid': createNode('composer-grid', { value: '1/16' }),
    'composer-syncopation': createNode('composer-syncopation', { value: '0.7' }),
    'composer-octave': createNode('composer-octave', { value: '0.2' }),
    'composer-ghost': createNode('composer-ghost', { checked: true }),
    'composer-velocity-min': createNode('composer-velocity-min', { value: '65' }),
    'composer-velocity-max': createNode('composer-velocity-max', { value: '118' }),
    'composer-avoid-kick': createNode('composer-avoid-kick', { checked: true }),
    'composer-kicks': createNode('composer-kicks', { value: '0, 1, 2, 3' }),
    'composer-preview': createNode('composer-preview', { disabled: false }),
    'composer-run': createNode('composer-run', { disabled: true }),
    'composer-adopt': createNode('composer-adopt', { disabled: true }),
    'composer-status': createNode('composer-status'),
    'composer-result': createNode('composer-result'),

    // Astra controls
    'astra-operation': createNode('astra-operation', { value: 'generate' }),
    'astra-role': createNode('astra-role', { value: 'bass' }),
    'astra-genre': createNode('astra-genre', { value: 'mutation_funk' }),
    'astra-mood': createNode('astra-mood', { value: '暗い、ファンキー' }),
    'astra-key': createNode('astra-key', { value: 'D#m' }),
    'astra-tempo': createNode('astra-tempo', { value: '124' }),
    'astra-bars': createNode('astra-bars', { value: '8' }),
    'astra-grid': createNode('astra-grid', { value: '1/16' }),
    'astra-syncopation': createNode('astra-syncopation', { value: '0.7' }),
    'astra-octave': createNode('astra-octave', { value: '0.2' }),
    'astra-velocity-min': createNode('astra-velocity-min', { value: '65' }),
    'astra-velocity-max': createNode('astra-velocity-max', { value: '118' }),
    'astra-note-min': createNode('astra-note-min', { value: '0.25' }),
    'astra-note-max': createNode('astra-note-max', { value: '0.75' }),
    'astra-variation-bar': createNode('astra-variation-bar', { value: '' }),
    'astra-kicks': createNode('astra-kicks', { value: '0, 1, 2, 3' }),
    'astra-source': createNode('astra-source', { value: 'none' }),
    'astra-ghost': createNode('astra-ghost', { checked: true }),
    'astra-avoid-kick': createNode('astra-avoid-kick', { checked: true }),
    'astra-prompt': createNode('astra-prompt', { value: 'キックは短め' }),
    'astra-send': createNode('astra-send', { disabled: false }),
    'astra-status': createNode('astra-status'),

    // MusicPlan controls referenced by invalidateComposerPreview
    'music-plan-approve': createNode('music-plan-approve', { disabled: true }),
    'music-plan-reject': createNode('music-plan-reject', { disabled: true }),
    'music-plan-verify': createNode('music-plan-verify', { disabled: true }),
    'music-plan-live': createNode('music-plan-live', { disabled: true }),
    'live-plan-pending': createNode('live-plan-pending'),
  };

  const composerInputs = [
    nodes['composer-role'],
    nodes['composer-key'],
    nodes['composer-tempo'],
    nodes['composer-bars'],
    nodes['composer-grid'],
    nodes['composer-syncopation'],
    nodes['composer-octave'],
    nodes['composer-ghost'],
    nodes['composer-velocity-min'],
    nodes['composer-velocity-max'],
    nodes['composer-avoid-kick'],
    nodes['composer-kicks'],
  ];

  const astraInputs = [
    nodes['astra-operation'],
    nodes['astra-role'],
    nodes['astra-genre'],
    nodes['astra-mood'],
    nodes['astra-key'],
    nodes['astra-tempo'],
    nodes['astra-bars'],
    nodes['astra-grid'],
    nodes['astra-syncopation'],
    nodes['astra-octave'],
    nodes['astra-velocity-min'],
    nodes['astra-velocity-max'],
    nodes['astra-note-min'],
    nodes['astra-note-max'],
    nodes['astra-variation-bar'],
    nodes['astra-kicks'],
    nodes['astra-source'],
    nodes['astra-ghost'],
    nodes['astra-avoid-kick'],
    nodes['astra-prompt'],
  ];

  const apiCalls = [];
  let postHandler = async () => ({ ok: true });

  const esc = (v) =>
    String(v ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');

  const post = async (url, payload) => {
    apiCalls.push({ method: 'POST', url, payload });
    return await postHandler(url, payload);
  };

  const blockStart = html.indexOf('    let composerPreview = null;');
  const blockEnd = html.indexOf('    $("music-plan-preview").onclick = async () => {');
  assert.ok(blockStart > 0 && blockEnd > blockStart, 'Could not find Astra/Composer JS block');

  const composerBtnStart = html.indexOf('    $("composer-preview").onclick = async () => {');
  const composerBtnEnd = html.indexOf('    $("composer-adopt").onclick = async () => {');
  assert.ok(
    composerBtnStart > 0 && composerBtnEnd > composerBtnStart,
    'Could not find composer-preview / composer-run handlers',
  );

  const script = `
    const state = { candidate: { candidate_id: "cand-1" } };
    ${html.slice(blockStart, blockEnd)}
    ${html.slice(composerBtnStart, composerBtnEnd)}
    globalThis.__getComposerPreview = () => composerPreview;
  `;

  const context = vm.createContext({
    $: (id) => nodes[id] || null,
    esc,
    post,
    document: {
      querySelectorAll(selector) {
        if (selector.includes('#midi-composer-view')) return composerInputs;
        if (selector.includes('#astra-params-view')) return astraInputs;
        return [];
      },
    },
    Math,
    Number,
    JSON,
    Boolean,
  });

  vm.runInContext(script, context);

  return {
    nodes,
    apiCalls,
    getComposerPreview: () => context.__getComposerPreview(),
    setPostHandler(fn) {
      postHandler = fn;
    },
  };
}

function astraPreviewOk(overrides = {}) {
  return {
    ok: true,
    provider: 'openai',
    model: 'gpt-6-astra',
    paid_api: true,
    cost: {
      allowed: true,
      spent_jpy: 12.5,
      estimate: { estimated_jpy: 1.85 },
    },
    ...overrides,
  };
}

function astraComposeOk(overrides = {}) {
  return {
    ok: true,
    provider: 'openai',
    model: 'gpt-6-astra',
    role: 'bass',
    note_count: 2,
    notes: [
      { pitch: 39, start: 0, duration: 0.5, velocity: 100, channel: 0 },
      { pitch: 46, start: 1, duration: 0.25, velocity: 90, channel: 0 },
    ],
    next_action: '候補を確認してください。',
    ...overrides,
  };
}

async function runTests() {
  console.log('Running Astra / MIDI Composer UI safety tests...');

  // 0. Static HTML structure check
  {
    assert.ok(html.includes('<section id="astra-params-view">'), 'Missing #astra-params-view');
    assert.ok(html.includes('id="astra-send"'), 'Missing #astra-send button');
    assert.ok(html.includes('id="astra-status"'), 'Missing #astra-status');
    console.log('  ✓ Static HTML checks passed');
  }

  // 1. #composer-preview followed by #astra-send must NOT call /api/midi/compose
  {
    const h = createHarness();
    h.setPostHandler(async (url) => {
      if (url === '/api/midi/preview') return astraPreviewOk();
      throw new Error(`Unexpected call to ${url}`);
    });

    await h.nodes['composer-preview'].onclick();
    assert.equal(h.apiCalls.length, 1);
    assert.equal(h.apiCalls[0].url, '/api/midi/preview');
    assert.equal(h.getComposerPreview().astraConfirmed, false);

    // Clicking #astra-send immediately after #composer-preview must treat this as first-click Astra confirmation
    await h.nodes['astra-send'].onclick();
    assert.equal(h.apiCalls.length, 2, 'Should run preview again for Astra confirmation, never compose');
    assert.equal(h.apiCalls[1].url, '/api/midi/preview');
    assert.ok(
      !h.apiCalls.some((c) => c.url === '/api/midi/compose'),
      'Must NEVER call /api/midi/compose without explicit Astra confirmation',
    );
    assert.equal(h.getComposerPreview().astraConfirmed, true);
    assert.equal(h.nodes['composer-run'].disabled, true);
    assert.match(h.nodes['astra-status'].textContent, /確認しました。推定 約1\.85円/);
    console.log('  ✓ Test 1: #composer-preview -> #astra-send does not bypass Astra confirmation');
  }

  // 2 & 3. #astra-send 1st click only previews/checks; 2nd click with same payload calls /api/midi/compose
  {
    const h = createHarness();
    h.setPostHandler(async (url) => {
      if (url === '/api/midi/preview') return astraPreviewOk();
      if (url === '/api/midi/compose') return astraComposeOk();
      throw new Error(`Unexpected call to ${url}`);
    });

    // 1st click: preview only
    await h.nodes['astra-send'].onclick();
    assert.equal(h.apiCalls.length, 1);
    assert.equal(h.apiCalls[0].url, '/api/midi/preview');
    assert.equal(h.nodes['astra-send'].disabled, false);
    assert.equal(h.getComposerPreview().astraConfirmed, true);

    // 2nd click with identical payload: compose
    await h.nodes['astra-send'].onclick();
    assert.equal(h.apiCalls.length, 2);
    assert.equal(h.apiCalls[1].url, '/api/midi/compose');
    assert.equal(h.apiCalls[1].payload.confirmed_paid, true);
    assert.equal(h.apiCalls[1].payload.constraints.genre, 'mutation_funk');
    assert.equal(h.getComposerPreview(), null, 'Preview state must be consumed after compose');
    assert.equal(h.nodes['astra-send'].disabled, false);
    assert.match(h.nodes['astra-status'].textContent, /gpt-6-astra: 2ノートを生成しました/);
    console.log('  ✓ Test 2 & 3: #astra-send 1st click confirms, 2nd click composes with confirmed_paid');
  }

  // 4. Non-gpt-6-astra models are rejected on #astra-send and never call /api/midi/compose
  {
    for (const nonAstraModel of ['deterministic', 'ollama', 'gpt-6.1-sol']) {
      const h = createHarness();
      h.setPostHandler(async (url) => {
        if (url === '/api/midi/preview') {
          return astraPreviewOk({
            provider: nonAstraModel === 'gpt-6.1-sol' ? 'openai' : nonAstraModel,
            model: nonAstraModel,
            paid_api: nonAstraModel === 'gpt-6.1-sol',
          });
        }
        throw new Error(`Should never call ${url} for model ${nonAstraModel}`);
      });

      await h.nodes['astra-send'].onclick();
      assert.equal(h.apiCalls.length, 1);
      assert.equal(h.apiCalls[0].url, '/api/midi/preview');
      assert.equal(h.getComposerPreview(), null);
      assert.equal(h.nodes['composer-run'].disabled, true);
      assert.match(h.nodes['astra-status'].textContent, new RegExp(`いまのモデルは ${nonAstraModel} です`));

      // Clicking a second time still only runs preview and rejects, never composes
      await h.nodes['astra-send'].onclick();
      assert.equal(h.apiCalls.length, 2);
      assert.equal(h.apiCalls[1].url, '/api/midi/preview');
      assert.ok(!h.apiCalls.some((c) => c.url === '/api/midi/compose'));
    }
    console.log('  ✓ Test 4: Non-gpt-6-astra models are rejected and never call /api/midi/compose');
  }

  // 5. Changing any input after 1st click invalidates confirmation; next click re-previews
  {
    const h = createHarness();
    h.setPostHandler(async (url) => {
      if (url === '/api/midi/preview') return astraPreviewOk();
      if (url === '/api/midi/compose') return astraComposeOk();
      throw new Error(`Unexpected call to ${url}`);
    });

    // 1st click confirms
    await h.nodes['astra-send'].onclick();
    assert.equal(h.getComposerPreview().astraConfirmed, true);

    // User edits an Astra parameter
    h.nodes['astra-prompt'].value = 'ベースをさらに重く';
    h.nodes['astra-prompt'].dispatchEvent({ type: 'input' });
    assert.equal(h.getComposerPreview(), null, 'Input change must clear composerPreview');
    assert.match(h.nodes['astra-status'].textContent, /内容が変わりました/);

    // Next click must re-preview, NOT compose
    await h.nodes['astra-send'].onclick();
    assert.equal(h.apiCalls.length, 2);
    assert.equal(h.apiCalls[1].url, '/api/midi/preview');
    assert.ok(!h.apiCalls.some((c) => c.url === '/api/midi/compose'));
    assert.equal(h.getComposerPreview().astraConfirmed, true);
    console.log('  ✓ Test 5: Input change invalidates confirmation and requires re-preview');
  }

  // 6 & 7. On /api/midi/compose failure (!data.ok) or network error, confirmation is cleared and buttons/status recover
  {
    // Case A: API returns { ok: false, error: ... }
    const h = createHarness();
    let composeAttempts = 0;
    h.setPostHandler(async (url) => {
      if (url === '/api/midi/preview') return astraPreviewOk();
      if (url === '/api/midi/compose') {
        composeAttempts += 1;
        return { ok: false, error: 'Astra生成エラー: レート制限' };
      }
      throw new Error(`Unexpected call to ${url}`);
    });

    // 1st click: confirm
    await h.nodes['astra-send'].onclick();
    assert.equal(h.getComposerPreview().astraConfirmed, true);

    // 2nd click: compose fails with ok: false
    await h.nodes['astra-send'].onclick();
    assert.equal(composeAttempts, 1);
    assert.equal(h.getComposerPreview(), null, 'composerPreview must be cleared on compose failure');
    assert.equal(h.nodes['astra-send'].disabled, false, '#astra-send must be re-enabled');
    assert.equal(h.nodes['composer-run'].disabled, true, '#composer-run must remain disabled');
    assert.match(h.nodes['astra-status'].innerHTML, /Astra生成エラー: レート制限/);

    // 3rd click: must NOT call compose directly; must re-preview first
    await h.nodes['astra-send'].onclick();
    assert.equal(composeAttempts, 1, 'Retry after failure must re-preview instead of composing directly');
    assert.equal(h.apiCalls[h.apiCalls.length - 1].url, '/api/midi/preview');
    assert.equal(h.getComposerPreview().astraConfirmed, true);

    // Case B: Network exception thrown during /api/midi/compose
    const hNet = createHarness();
    let netComposeAttempts = 0;
    hNet.setPostHandler(async (url) => {
      if (url === '/api/midi/preview') return astraPreviewOk();
      if (url === '/api/midi/compose') {
        netComposeAttempts += 1;
        throw new Error('Network disconnected');
      }
      throw new Error(`Unexpected call to ${url}`);
    });

    await hNet.nodes['astra-send'].onclick();
    assert.equal(hNet.getComposerPreview().astraConfirmed, true);

    await hNet.nodes['astra-send'].onclick();
    assert.equal(netComposeAttempts, 1);
    assert.equal(hNet.getComposerPreview(), null, 'composerPreview must be cleared on network exception');
    assert.equal(hNet.nodes['astra-send'].disabled, false, '#astra-send must not remain disabled');
    assert.doesNotMatch(
      hNet.nodes['astra-status'].textContent,
      /MIDI候補を生成中/,
      'Status must not remain stuck on loading text',
    );
    assert.match(hNet.nodes['astra-status'].textContent, /MIDI候補の生成に失敗しました/);

    // Next click must re-preview, not compose
    await hNet.nodes['astra-send'].onclick();
    assert.equal(netComposeAttempts, 1, 'Click after network exception must re-preview');
    assert.equal(hNet.apiCalls[hNet.apiCalls.length - 1].url, '/api/midi/preview');

    // Case C: Network exception thrown during /api/midi/preview
    const hPrevErr = createHarness();
    hPrevErr.setPostHandler(async () => {
      throw new Error('Preview network error');
    });
    await hPrevErr.nodes['composer-preview'].onclick();
    assert.equal(hPrevErr.getComposerPreview(), null);
    assert.equal(hPrevErr.nodes['composer-preview'].disabled, false);
    assert.equal(hPrevErr.nodes['composer-run'].disabled, true);
    assert.match(hPrevErr.nodes['composer-status'].textContent, /確認に失敗しました/);

    console.log('  ✓ Test 6 & 7: Compose/preview failure & network exceptions clear confirmation and restore UI state');
  }

  console.log('All Astra / MIDI Composer UI safety tests passed!');
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
