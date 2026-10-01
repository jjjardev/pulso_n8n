// ===========================================================================
//  Node 2b — CORRECTED quote-aware CSV parser (FALLBACK ONLY)
//
//  USE THIS ONLY IF the standard "Extract From File" node (Node 2) cannot
//  handle the upload on your n8n version. Node 2 is the primary path and
//  handles quoting properly; do not wire this in by default.
//
//  WHY THE DOC'S VERSION IS WRONG
//  The pipeline doc (section 2, Node 2b) does:
//
//      const cols = l.split(',');
//
//  That breaks on any quoted field containing a comma. Given the doc's own
//  test CSV, a review like:
//
//      "Masarap, mura, at sulit na sulit!"
//
//  is split at the commas INSIDE the quotes, so the review text is truncated
//  to `Masarap` and the remaining fragments are read as separate columns. The
//  model then scores "Masarap" alone and the report shows a mangled comment.
//  With enough commas it can even throw off the column index entirely, so the
//  parser silently reads the WRONG COLUMN as the review.
//
//  The parser below is RFC4180-aware: it walks the line character by
//  character, only treating a comma as a separator when it is outside quotes,
//  and it doubles up on "" which is how a literal quote is escaped inside a
//  quoted field.
//
//  TO USE
//    Replace Node 2's body with this, or insert it as a Code node between
//    Node 1 and Node 3 (Run Once for All Items, mode: runOnceForAllItems).
//    It reads the binary field 'reviews_file', which is the Form Trigger's
//    Field Label - NOT 'data'.
//
//  It returns { review } for compatibility with Node 3, which also accepts
//  text/comment/content/feedback aliases and any single string field.
// ===========================================================================

const BINARY_FIELD = 'reviews_file';

// --- 1. Read the uploaded file into a string --------------------------------
const bin = items[0].binary[BINARY_FIELD];
if (!bin) {
  throw new Error(
    `No binary field named "${BINARY_FIELD}" on the incoming item. `
    + `Found: ${Object.keys(items[0].binary || {}).join(', ') || '(none)'}. `
    + `The file arrives under the Form Trigger's FIELD LABEL, not "data".`
  );
}

const raw = Buffer.from(bin.data, 'base64').toString('utf-8');

// Strip a UTF-8 BOM. Excel on Windows writes one, and it would otherwise end
// up glued to the first header name, turning "review" into "\ufeffreview" and
// breaking the column lookup below.
const text = raw.replace(/^\uFEFF/, '');

// --- 2. RFC4180 line splitter ------------------------------------------------
// Splits on newlines that are NOT inside a quoted field, so a review
// containing a literal newline inside quotes stays in one piece.
//
// CRITICAL: this function PRESERVES the quote characters it walks past. It
// only tracks whether it is inside quotes, so that it knows which newlines are
// real row boundaries. Stripping the quotes here is a real bug that was made
// and caught: with the quotes removed, the field splitter below sees a bare
// string containing commas, has no way to know those commas are inside a
// quoted field, and silently falls back to naive comma splitting - producing
// exactly the corruption this whole file exists to prevent. Quote REMOVAL is
// splitFields' job, and only that.
function splitRows(s) {
  const rows = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '"') {
      // Toggle, but KEEP the character. A doubled "" toggles twice, which
      // correctly leaves us outside quotes after an escaped quote.
      inQuotes = !inQuotes;
      cur += ch;
    } else if (!inQuotes && (ch === '\n' || ch === '\r')) {
      if (ch === '\r' && s[i + 1] === '\n') i++;
      rows.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  if (cur.length) rows.push(cur);
  return rows;
}

// --- 3. RFC4180 field splitter ------------------------------------------------
// Unlike String.split(','), this only splits on commas outside quotes.
function splitFields(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; }
        else { inQuotes = false; }
      } else { cur += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      out.push(cur);
      cur = '';
    } else { cur += ch; }
  }
  out.push(cur);
  return out;
}

const rows = splitRows(text).filter(r => r.trim().length > 0);
if (rows.length === 0) {
  throw new Error('The uploaded CSV is empty - no header row and no data rows.');
}

const headers = splitFields(rows[0]).map(h => h.trim().toLowerCase());

// --- 4. Find the review column ----------------------------------------------
// Same alias list as Node 3, plus a substring fallback so a column called
// "customer_review_text" or "review_body" is still recognised.
const ALIASES = ['review', 'text', 'comment', 'content', 'feedback',
                 'review_text', 'reviewtext', 'body', 'review_body',
                 'message', 'remarks', 'comment_text'];

let textIdx = headers.findIndex(h => ALIASES.includes(h));
if (textIdx === -1) {
  textIdx = headers.findIndex(h => h.includes('review') || h.includes('comment'));
}
if (textIdx === -1) {
  throw new Error(
    `No review/text column found. Columns present: ${headers.join(', ')}. `
    + `Rename your column to one of: ${ALIASES.join(', ')}.`
  );
}

// --- 5. Emit one item per data row ------------------------------------------
return rows.slice(1).map((line, i) => {
  const cols = splitFields(line);
  return {
    json: {
      review: (cols[textIdx] ?? '').trim(),
      // Preserve the rest so a report could later show a rating or a date.
      _row: i + 2,
      _columns: headers.reduce((acc, h, j) => {
        acc[h] = (cols[j] ?? '').trim();
        return acc;
      }, {}),
    },
  };
});
