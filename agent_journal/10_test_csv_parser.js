#!/usr/bin/env node
/**
 * Test the corrected Node 2b quote-aware CSV parser.
 *
 * The parser in snippets/node2b_quote_aware_csv_parser.js exists to fix a real
 * bug in the source doc, which splits lines on every comma and therefore
 * mangles any quoted review containing a comma. A fix for a parsing bug that
 * is itself untested is not a fix, so this exercises it against the exact
 * cases that break the doc's version, plus a few more.
 *
 * Run:  node 10_test_csv_parser.js
 */

const fs = require('fs');
const path = require('path');

const parserPath = path.join(__dirname, 'snippets',
  'node2b_quote_aware_csv_parser.js');
const src = fs.readFileSync(parserPath, 'utf-8');

// The snippet is written as a bare top-level Code-node body (it uses `return`,
// which only a function body allows). Wrap it exactly the way n8n does: the
// Code node injects `items`, `$`, `$input` and `Buffer` as parameters.
const makeParser = new Function('items', 'Buffer', `
  ${src}
`);

let passed = 0, failed = 0;
function check(name, cond, detail = '') {
  if (cond) { passed++; console.log(`  [PASS] ${name}`); }
  else { failed++; console.log(`  [FAIL] ${name}   ${detail}`); }
}

function run(csv, field = 'reviews_file') {
  const b64 = Buffer.from(csv, 'utf-8').toString('base64');
  const items = [{ json: {}, binary: { [field]: { data: b64, fileName: 'r.csv',
    mimeType: 'text/csv' } } }];
  return makeParser(items, Buffer);
}

console.log('='.repeat(78));
console.log('Node 2b quote-aware CSV parser tests');
console.log('='.repeat(78));

// --- T1: the case that breaks the doc's parser ----------------------------
console.log("\nT1. Quoted field containing a comma (the doc's bug)");
{
  const csv = 'review\n"Masarap, mura, at sulit na sulit!"\n';
  const out = run(csv);
  check('one row parsed', out.length === 1, `got ${out.length}`);
  // The doc's naive split(',') would yield "Masarap" here.
  check('commas inside quotes preserved',
    out[0].json.review === 'Masarap, mura, at sulit na sulit!',
    JSON.stringify(out[0].json.review));
}

// --- T2: multiple quoted fields with commas -------------------------------
console.log('\nT2. Multiple quoted fields, commas in each');
{
  const csv = 'name,review,rating\n'
    + '"Juan, Jr","Great food, fast service, will return",5\n'
    + '"Maria","Slow, but tasty",3\n';
  const out = run(csv);
  check('two rows', out.length === 2, `got ${out.length}`);
  check('row 0 review intact',
    out[0].json.review === 'Great food, fast service, will return',
    JSON.stringify(out[0].json.review));
  check('row 1 review intact', out[1].json.review === 'Slow, but tasty',
    JSON.stringify(out[1].json.review));
  // THE KEY REGRESSION: the naive parser reads the wrong COLUMN here.
  check('review column index not shifted by the name field',
    !out[0].json.review.startsWith('Juan'),
    `read name column as review: ${out[0].json.review}`);
  check('other columns preserved for later use',
    out[0].json._columns.name === 'Juan, Jr'
    && out[0].json._columns.rating === '5',
    JSON.stringify(out[0].json._columns));
}

// --- T3: escaped double-quotes -------------------------------------------
console.log('\nT3. Escaped quotes ("" inside a quoted field)');
{
  const csv = 'review\n"She said ""sulit"" three times"\n';
  const out = run(csv);
  check('"" collapses to a single "',
    out[0].json.review === 'She said "sulit" three times',
    JSON.stringify(out[0].json.review));
}

// --- T4: newline inside a quoted field ------------------------------------
console.log('\nT4. Literal newline inside a quoted field');
{
  const csv = 'review\n"Line one\nLine two"\n';
  const out = run(csv);
  check('stays as ONE row (not split on the newline)', out.length === 1,
    `got ${out.length} rows`);
  check('newline preserved inside the text',
    (out[0].json.review || '').includes('Line one'),
    JSON.stringify(out[0].json.review));
}

// --- T5: Windows line endings and BOM -------------------------------------
console.log('\nT5. Windows CRLF line endings and a UTF-8 BOM');
{
  const csv = '\uFEFFreview\r\n"Sulit, sobra"\r\n"Bad, sayang"\r\n';
  const out = run(csv);
  check('BOM stripped from the first header', out.length === 2,
    `got ${out.length}`);
  check('CRLF rows split correctly',
    out[0].json.review === 'Sulit, sobra' && out[1].json.review === 'Bad, sayang',
    JSON.stringify(out.map(o => o.json.review)));
  check('no stray carriage returns',
    out.every(o => !o.json.review.includes('\r')),
    'a \\r leaked into a review');
}

// --- T6: column aliases and the substring fallback ------------------------
console.log('\nT6. Column-name detection');
{
  for (const col of ['review', 'text', 'comment', 'content', 'feedback',
                     'Review', 'REVIEW', 'review_text']) {
    const out = run(`${col}\n"A real, review here"\n`);
    check(`finds column "${col}"`, out.length === 1
      && out[0].json.review === 'A real, review here',
      JSON.stringify(out));
  }
  // Substring fallback for non-exact names
  const out = run('customer_review_body\n"Nice, very nice"\n');
  check('substring fallback finds customer_review_body',
    out.length === 1 && out[0].json.review === 'Nice, very nice',
    JSON.stringify(out));
}

// --- T7: error handling ----------------------------------------------------
console.log('\nT7. Error messages');
{
  let msg = null;
  try { run(''); } catch (e) { msg = e.message; }
  check('empty file gives a clear error',
    msg && /empty/i.test(msg), String(msg));

  msg = null;
  try { run('foo,bar\n1,2\n'); } catch (e) { msg = e.message; }
  check('no review column lists the columns it found',
    msg && /foo, bar/.test(msg) && /Rename/i.test(msg), String(msg));

  msg = null;
  try { run('review\n"ok"\n', 'wrong_field_name'); } catch (e) { msg = e.message; }
  check('wrong binary field name explains the Field Label gotcha',
    msg && /FIELD LABEL/i.test(msg) && /wrong_field_name/.test(msg),
    String(msg));
}

// --- T8: unquoted values still work ---------------------------------------
console.log('\nT8. Plain unquoted CSV (the common case)');
{
  const csv = 'review\nSulit na sulit\nMasarap\n';
  const out = run(csv);
  check('two unquoted rows parsed',
    out.length === 2 && out[0].json.review === 'Sulit na sulit',
    JSON.stringify(out.map(o => o.json.review)));
}

console.log('\n' + '='.repeat(78));
console.log(`RESULT: ${passed} passed, ${failed} failed`);
console.log('='.repeat(78));
process.exit(failed === 0 ? 0 : 1);
