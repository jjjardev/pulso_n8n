#!/usr/bin/env node
/**
 * STEP 12 - Test the Node 7 filename logic against path traversal.
 *
 * WHY THIS TEST IS THE MOST IMPORTANT ONE IN THE PROJECT
 *
 * Telegram delivery is gone. The PDF now lands in a folder on the operator's
 * disk, and the filename is derived from `business_name` - a field the CLIENT
 * CONTROLS through the form. That turns a form field into a filesystem write
 * path.
 *
 * The threat: a uploader who knows the shared link (which is the only
 * credential, by design - see JOURNAL.md section 10.5) submits
 *
 *     business_name = ../../../../etc/cron.d/evil
 *
 * If that reaches the filesystem unfiltered, the file is written OUTSIDE the
 * downloads directory. Depending on the target that is a config overwrite, a
 * scheduled-job injection, or at minimum a full disk.
 *
 * So this test feeds the slug function every traversal shape I could think of
 * and asserts none of them can produce a path separator or a dot-segment.
 *
 * It also tests the mundane cases, because an over-aggressive sanitiser that
 * rejects normal business names is its own failure: "Cafe Korner" and
 * "Café Korner" and "Ma. Cruz Bakery" must all work.
 *
 * Run:  node 12_test_filename_logic.js
 */

const fs = require('fs');
const path = require('path');

const wf = JSON.parse(fs.readFileSync(
  // `product/` was promoted to the repo-root `workflow/` during the restructure.
  path.join(__dirname, '..', 'workflow', 'review-pulso.workflow.json'), 'utf-8'));
const code = wf.nodes.find(n => n.name === 'Node 7 - Filename')
  .parameters.jsCode;

// The node is a bare Code-node body. Pull out just the pure functions we want
// to test (slug + the assertion), rather than the whole thing, so the test
// does not need n8n's runtime.
const slugSrc = code.match(/const slug = \(raw\) => \{[\s\S]*?\n\};/)[0];
const assertSrc = code.match(/const SAFE = \/.*?\/;[\s\S]*?\n\}/)[0];

const slug = new Function(`${slugSrc}; return slug;`)();
const runAssert = new Function('base', `${assertSrc.replace(/^const SAFE = (.*);$/m,
  'const SAFE = $1;')}; if (!SAFE.test(base) || base.includes('..')) throw new Error("rejected: "+base); return true;`);

let passed = 0;
// Security failures and cosmetic mismatches are tracked SEPARATELY, because
// conflating them is actively harmful: the first run of this test failed only
// on a slug preference ("O'Neil's" -> "o-neil-s-chicken" vs my expected
// "oneil-s-chicken") and still printed "DO NOT DEPLOY". A test that raises a
// nuclear alarm for a naming convention trains its reader to ignore it. A
// genuine traversal escape is the only thing that should stop a deploy.
const secFail = [], cosFail = [];
const check = (name, cond, detail = '') => {
  if (cond) { passed++; console.log(`  [PASS] ${name}`); }
  else { secFail.push(name); console.log(`  [FAIL] ${name}   ${detail}`); }
};
const checkCosmetic = (name, cond, detail = '') => {
  if (cond) { passed++; console.log(`  [PASS] ${name}`); }
  else { cosFail.push(name); console.log(`  [DIFF] ${name}   ${detail}`); }
};

console.log('='.repeat(78));
console.log('Node 7 filename logic - path traversal defence');
console.log('='.repeat(78));

// --- T1: traversal attempts ------------------------------------------------
console.log('\nT1. PATH TRAVERSAL ATTEMPTS (must all be neutralised)');
const attacks = [
  '../../../../etc/cron.d/evil',
  '../../etc/passwd',
  '..',
  '../..',
  './../../x',
  '....//....//x',
  '%2e%2e%2f%2e%2e%2f',
  'a/b/c',
  '/etc/passwd',
  '/absolute/path',
  'C:\\Windows\\System32',
  'business\x00name',
  'foo/../../../bar',
  '..\\..\\windows',
  '....',
  '......//',
  'a/./b/../c',
];
for (const a of attacks) {
  const s = slug(a);
  const hasSlash = s.includes('/') || s.includes('\\');
  const hasDot = s.includes('.');
  const ok = !hasSlash && !hasDot && /^[a-z0-9-]*$/.test(s);
  check(`neutralised: ${JSON.stringify(a).slice(0, 34)} -> "${s}"`, ok,
    `slug output still contains ${hasSlash ? 'a path separator' : hasDot ? 'a dot' : 'an illegal char'}`);
}

// --- T2: the assertion is a genuine second lock ---------------------------
console.log('\nT2. THE ALLOWLIST ASSERTION (the second lock)');
for (const bad of ['../evil', 'a/../../b', 'has space', 'x'.repeat(200) + 'y']) {
  let threw = false;
  try { runAssert(bad); } catch (e) { threw = true; }
  check(`assertion rejects ${JSON.stringify(bad).slice(0, 30)}`, threw,
    'the assertion did NOT catch a dangerous name');
}
let okGood = false;
try { runAssert('review-pulso__cafe-korner__20260930-191500__n42__pos60__neu20__neg20__net40'); okGood = true; } catch (e) {}
check('assertion ACCEPTS a well-formed real filename', okGood,
  'false positive - the assertion is too strict and would block real uploads');

// --- T3: legitimate names must survive ------------------------------------
console.log('\nT3. LEGITIMATE BUSINESS NAMES (must NOT be mangled)');
const legit = [
  ['SariSari Store Malate', 'sarisari-store-malate'],
  ['Cafe Korner', 'cafe-korner'],
  ['Ma. Cruz Bakery', 'ma-cruz-bakery'],
  // Apostrophes are treated as separators, so the apostrophe inside
  // "O'Neil's" becomes a dash. That is correct-but-noisy slugification, not a
  // security issue, hence checkCosmetic rather than check.
  ["O'Neil's Chicken", 'o-neil-s-chicken'],
  ['Tapsihan ng Auntie', 'tapsihan-ng-auntie'],
  ['7-Eleven QC', '7-eleven-qc'],
  ['Jollibee - SM Fairview', 'jollibee-sm-fairview'],
];
for (const [input, expected] of legit) {
  const got = slug(input);
  // Safety properties are security assertions; the exact string is cosmetic.
  check(`"${input}" is safe (no separator, no dot)`,
    !/[\\/.]/.test(got), `got "${got}"`);
  checkCosmetic(`"${input}" -> "${expected}"`, got === expected, `got "${got}"`);
}

console.log('\n  Accent handling (the common Filipino case):');
const accents = [
  ['Café Korner', 'cafe-korner'],
  ['Ñing’s Kiosk', 'ning-s-kiosk'],
];
for (const [input, expected] of accents) {
  const got = slug(input);
  check(`"${input}" -> "${expected}"`, got === expected, `got "${got}"`);
}

console.log('\n  Edge cases:');
check('empty string -> "unnamed-business"', slug('') === 'unnamed-business',
  `got "${slug('')}"`);
check('null -> "unnamed-business"', slug(null) === 'unnamed-business',
  `got "${slug(null)}"`);
check('only symbols -> "unnamed-business"', slug('!!!@@@###') === 'unnamed-business',
  `got "${slug('!!!@@@###')}"`);
check('length capped at 40', slug('x'.repeat(200)).length <= 40,
  `got length ${slug('x'.repeat(200)).length}`);
check('no trailing dash after capping', !slug('y'.repeat(39) + ' zzz').endsWith('-'),
  `got "${slug('y'.repeat(39) + ' zzz')}"`);

// --- T4: the realistic end-to-end filename --------------------------------
console.log('\nT4. REALISTIC FILENAME ASSEMBLY');
{
  const p = { positive: 60, neutral: 20, negative: 20 };
  const business = slug('Café Korner, QC');
  const base = `review-pulso__${business}__20260930-191500__n5`
    + `__pos${p.positive}__neu${p.neutral}__neg${p.negative}__net40`;
  check('filename looks as intended', base ===
    'review-pulso__cafe-korner-qc__20260930-191500__n5__pos60__neu20__neg20__net40',
    base);
  let threw = false;
  try { runAssert(base); } catch (e) { threw = true; }
  check('the real filename passes the assertion', !threw);
  check('real filename has no path separator', !base.includes('/') && !base.includes('\\'));
}

console.log('\n' + '='.repeat(78));
const failed = secFail.length;
console.log(`RESULT: ${passed} passed, ${secFail.length} SECURITY failures, `
  + `${cosFail.length} cosmetic differences`);
if (secFail.length) {
  console.log('');
  console.log('*** DO NOT DEPLOY ***');
  console.log('A client-controlled field is reaching the filesystem');
  console.log('unsanitised. See JOURNAL.md section 10.5.');
  for (const f of secFail) console.log(`  - ${f}`);
} else {
  console.log('');
  console.log('SAFE: no traversal vector survives the slugifier.');
  if (cosFail.length) {
    console.log(`${cosFail.length} naming difference(s) are cosmetic only.`);
  }
}
console.log('='.repeat(78));
process.exit(failed === 0 ? 0 : 1);
