/**
 * Regression tests for Jarvis MIDI clip preview UI validation in index.html.
 *
 * Constraints tested:
 * - Incomplete data (missing, null, non-array) must never be treated as add/delete.
 * - Invalid note elements (type mismatch, out of range) must trigger comparison error.
 * - Note comparison: attribute-based deterministic mapping with safe ambiguity detection.
 * - Live changed / applied flag prevents misleading preview badge.
 * - Empty arrays vs identical notes vs modifications vs additions vs deletions.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('src/kihachi_mcp/studio/static/index.html', 'utf8');
const startMarker = '    function isValidNote(note) {';
const endMarker = '    function showJarvisAnswer(data) {';
const startIndex = html.indexOf(startMarker);
const endIndex = html.indexOf(endMarker);

assert.ok(startIndex >= 0, 'Could not find isValidNote start marker in index.html');
assert.ok(endIndex > startIndex, 'Could not find showJarvisAnswer end marker in index.html');

const source = html.slice(startIndex, endIndex);

// Setup DOM mock environment
function createMockEnv() {
  const nodes = {
    'jarvis-clip-preview-card': { hidden: true },
    'jarvis-clip-preview-meta': { innerHTML: '', innerText: '' },
    'jarvis-clip-preview-diff-area': { innerHTML: '', innerText: '' },
    'jarvis-clip-preview-all-area': { innerHTML: '', innerText: '' },
    'jarvis-clip-preview-all-summary': { textContent: '' },
  };

  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );

  const context = vm.createContext({
    $: (id) => nodes[id],
    esc,
  });

  vm.runInContext(source, context);

  const renderJarvisClipPreview = vm.runInContext('renderJarvisClipPreview', context);
  const isValidNote = vm.runInContext('isValidNote', context);

  return { nodes, renderJarvisClipPreview, isValidNote };
}

const validNoteA = { pitch: 60, start_time: 0.0, duration: 0.25, velocity: 100, probability: 1.0 };
const validNoteB = { pitch: 64, start_time: 1.0, duration: 0.5, velocity: 92, probability: 0.8 };
const modifiedNoteA = { pitch: 60, start_time: 0.0, duration: 0.25, velocity: 106, probability: 1.0 };

let totalTests = 0;
let passedTests = 0;

function runTest(name, fn) {
  totalTests++;
  try {
    fn();
    passedTests++;
    console.log(`  PASS: ${name}`);
  } catch (err) {
    console.error(`  FAIL: ${name}`);
    throw err;
  }
}

console.log('--- 1. Incomplete / Missing / Null / Non-array tests ---');

runTest('Missing original_notes (must show comparison impossible, no add/delete)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', preview_notes: [validNoteA] }
  });
  assert.equal(nodes['jarvis-clip-preview-card'].hidden, false);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
  assert.equal(nodes['jarvis-clip-preview-all-summary'].textContent, '全ノートの詳細（表示不能）');
  assert.equal(nodes['jarvis-clip-preview-all-area'].innerHTML, '');
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Missing preview_notes (must show comparison impossible, no add/delete)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA] }
  });
  assert.equal(nodes['jarvis-clip-preview-card'].hidden, false);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
  assert.equal(nodes['jarvis-clip-preview-all-summary'].textContent, '全ノートの詳細（表示不能）');
  assert.equal(nodes['jarvis-clip-preview-all-area'].innerHTML, '');
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Null original_notes (must show comparison impossible)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: null, preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Null preview_notes (must show comparison impossible)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: null }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Non-array original_notes string (must show comparison impossible)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: 'invalid_string', preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
});

runTest('Non-array preview_notes object (must show comparison impossible)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: { pitch: 60 } }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（ノートデータが不完全です）。/);
});

console.log('\n--- 2. Invalid note elements (type, null, range) tests ---');

runTest('Null element in original_notes (must trigger invalid data error)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [null], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Primitive element in preview_notes (must trigger invalid data error)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [123] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid pitch: negative (-1)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, pitch: -1 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid pitch: out of MIDI range (128)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, pitch: 128 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid pitch: string type ("60")', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, pitch: '60' }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid start_time: negative (-0.1)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, start_time: -0.1 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid duration: zero (0)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, duration: 0 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid velocity: zero (0)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, velocity: 0 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid velocity: float (100.5)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, velocity: 100.5 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid probability: out of 0-1 range (1.2)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, probability: 1.2 }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
});

runTest('Invalid pitch: NaN', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, pitch: NaN }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid pitch: Infinity', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, pitch: Infinity }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid start_time: NaN', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [{ ...validNoteA, start_time: NaN }] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid start_time: Infinity', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [{ ...validNoteA, start_time: Infinity }] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid duration: NaN', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, duration: NaN }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid duration: Infinity', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, duration: Infinity }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid velocity: NaN', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [{ ...validNoteA, velocity: NaN }] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid velocity: Infinity', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [{ ...validNoteA, velocity: Infinity }] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid probability: NaN', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, probability: NaN }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Invalid probability: Infinity', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [{ ...validNoteA, probability: Infinity }], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（不正なノートデータが含まれています）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

console.log('\n--- 3. Empty arrays and valid comparison tests ---');

runTest('Valid empty arrays (original: [], preview: [])', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [], preview_notes: [] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /ノートデータがありません。/);
  assert.equal(nodes['jarvis-clip-preview-all-summary'].textContent, '全ノートの詳細を表示（0音）');
});

runTest('Valid identical notes (no changes)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', original_notes: [validNoteA], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /変更ノートはありません（変更なし）。/);
  assert.match(nodes['jarvis-clip-preview-all-summary'].textContent, /全1音、差分0音/);
  assert.match(nodes['jarvis-clip-preview-all-area'].innerHTML, /60/);
});

runTest('Valid velocity modification (original velocity 100 -> preview 106)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'velocity_contrast', original_notes: [validNoteA], preview_notes: [modifiedNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[変更\]/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /#0/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /100.*→.*106/);
});

runTest('Valid note addition: only added note appears in diff', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_add', original_notes: [validNoteA], preview_notes: [validNoteA, validNoteB] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /#1/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /64/);
});

runTest('Valid note deletion: only deleted note appears in diff', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_delete', original_notes: [validNoteA, validNoteB], preview_notes: [validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[削除\]/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /#1/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /64/);
});

console.log('\n--- 4. Live changed badge / Safety flag tests ---');

runTest('Live unchanged (shows safe preview badge)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', live_changed: false, applied_to_live: false, original_notes: [], preview_notes: [] }
  });
  assert.match(nodes['jarvis-clip-preview-meta'].innerHTML, /プレビュー表示（Live 未変更・元データ保持）/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-meta'].innerHTML, /Live に適用済みのデータです/);
});

runTest('Live changed flag on preview object (shows warning badge, prevents misleading)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', live_changed: true, original_notes: [], preview_notes: [] }
  });
  assert.match(nodes['jarvis-clip-preview-meta'].innerHTML, /【注意】Live に適用済みのデータです（プレビューではありません）/);
});

runTest('applied_to_live flag on preview object (shows warning badge, prevents misleading)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test', applied_to_live: true, original_notes: [], preview_notes: [] }
  });
  assert.match(nodes['jarvis-clip-preview-meta'].innerHTML, /【注意】Live に適用済みのデータです（プレビューではありません）/);
});

runTest('live_changed flag on root data object (shows warning badge, prevents misleading)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    live_changed: true,
    clip_preview: { action: 'test', original_notes: [], preview_notes: [] }
  });
  assert.match(nodes['jarvis-clip-preview-meta'].innerHTML, /【注意】Live に適用済みのデータです（プレビューではありません）/);
});

console.log('\n--- 5. Card visibility tests ---');

runTest('Null / empty data hides the preview card', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview(null);
  assert.equal(nodes['jarvis-clip-preview-card'].hidden, true);
  renderJarvisClipPreview({});
  assert.equal(nodes['jarvis-clip-preview-card'].hidden, true);
  renderJarvisClipPreview({ plan: {} });
  assert.equal(nodes['jarvis-clip-preview-card'].hidden, true);
});

console.log('\n--- 6. Reordering, insertion/deletion, velocity modification, duplicate notes, and ambiguity tests ---');

const validNoteC = { pitch: 67, start_time: 2.0, duration: 0.5, velocity: 90, probability: 1.0 };

runTest('Note reordering (shuffle): notes in different order show no diff', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_reorder', original_notes: [validNoteA, validNoteB], preview_notes: [validNoteB, validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /変更ノートはありません（変更なし）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Insertion in the middle of notes array: only inserted note is added, other notes unaffected', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_insert', original_notes: [validNoteA, validNoteC], preview_notes: [validNoteA, validNoteB, validNoteC] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /差分のあるノート（1音 \/ 全3音）/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\].*#1/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[変更\]|\[削除\]/);
});

runTest('Deletion in the middle of notes array: only deleted note is removed, other notes unaffected', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_delete_middle', original_notes: [validNoteA, validNoteB, validNoteC], preview_notes: [validNoteA, validNoteC] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /差分のあるノート（1音 \/ 全3音）/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[削除\].*#1/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[変更\]|\[追加\]/);
});

runTest('Velocity modification with shuffled array: accurately matches note and shows velocity change', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'velocity_contrast', original_notes: [validNoteA, validNoteB], preview_notes: [validNoteB, modifiedNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /差分のあるノート（1音 \/ 全2音）/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[変更\].*#0/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /100.*→.*106/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]/);
});

runTest('Duplicate notes with identical attributes (same start_time and pitch): matches all without error', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_dup_identical', original_notes: [validNoteA, validNoteA], preview_notes: [validNoteA, validNoteA] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /変更ノートはありません（変更なし）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Duplicate notes where one is modified and one unchanged: deterministic match succeeds', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: { action: 'test_dup_modify', original_notes: [validNoteA, modifiedNoteA], preview_notes: [validNoteA, { ...validNoteA, velocity: 112 }] }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /差分のあるノート（1音 \/ 全2音）/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[変更\]/);
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /106.*→.*112/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]/);
});

runTest('Duplicate notes with ambiguous modification (unequal counts): marks comparison impossible', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: {
      action: 'test_dup_ambiguous_counts',
      original_notes: [
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 80 },
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 100 },
      ],
      preview_notes: [
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 90 },
      ],
    }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（同時刻・同音高の重複ノートが含まれ、対応付けが曖昧です）。/);
  assert.doesNotMatch(nodes['jarvis-clip-preview-diff-area'].innerHTML, /\[追加\]|\[削除\]|\[変更\]/);
});

runTest('Duplicate notes with ambiguous modification (equal counts > 1, all different): marks comparison impossible', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: {
      action: 'test_dup_ambiguous_all_diff',
      original_notes: [
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 80 },
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 100 },
      ],
      preview_notes: [
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 85 },
        { pitch: 60, start_time: 0.0, duration: 1.0, velocity: 105 },
      ],
    }
  });
  assert.match(nodes['jarvis-clip-preview-diff-area'].innerHTML, /プレビュー比較不能（同時刻・同音高の重複ノートが含まれ、対応付けが曖昧です）。/);
});

console.log('\n--- 7. HTML escaping and XSS safety tests ---');

runTest('Action name and summary with HTML characters are safely escaped (XSS prevention)', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: {
      action: '<script>alert("xss-action")</script>',
      summary: '<img src=x onerror=alert("xss-summary")>& "quoted"',
      original_notes: [],
      preview_notes: [],
    }
  });
  const metaHtml = nodes['jarvis-clip-preview-meta'].innerHTML;
  assert.doesNotMatch(metaHtml, /<script>/i);
  assert.doesNotMatch(metaHtml, /<img\b/i);
  assert.match(metaHtml, /&lt;script&gt;alert\(&quot;xss-action&quot;\)&lt;\/script&gt;/);
  assert.match(metaHtml, /&lt;img src=x onerror=alert\(&quot;xss-summary&quot;\)&gt;&amp; &quot;quoted&quot;/);
});

console.log('\n--- 8. Out of range target_bars display test ---');

runTest('out_of_range preview displays "範囲外（変更なし）" without misleading as "全小節"', () => {
  const { nodes, renderJarvisClipPreview } = createMockEnv();
  renderJarvisClipPreview({
    clip_preview: {
      action: 'reduce_density',
      out_of_range: true,
      target_bars: null,
      requested_bars: { start: 5, end: 8 },
      summary: '5–8小節はクリップ（4小節）の範囲外です。',
      original_notes: [validNoteA],
      preview_notes: [validNoteA],
    }
  });
  const metaHtml = nodes['jarvis-clip-preview-meta'].innerHTML;
  assert.match(metaHtml, /<b>対象:<\/b> 範囲外（変更なし）/);
  assert.doesNotMatch(metaHtml, /<b>対象:<\/b> 全小節/);
});

console.log(`\nAll ${passedTests}/${totalTests} tests passed successfully!`);
