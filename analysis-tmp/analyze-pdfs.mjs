import fs from 'fs';
import path from 'path';
import { createRequire } from 'module';
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs';

const require = createRequire(import.meta.url);

const files = [
  path.resolve('..', 'HYD_OtherType_2026-2027_10250.pdf'),
  path.resolve('..', 'HYD_Payment_5589_UTRStatement_IN42631151879720#1_2026-2027.pdf'),
];

function meaningfulText(text) {
  const cleaned = (text || '').replace(/\s+/g, ' ').trim();
  const alnum = cleaned.replace(/[^A-Za-z0-9]/g, '');
  return {
    charCount: cleaned.length,
    alnumCount: alnum.length,
    isMeaningful: alnum.length >= 40,
    preview: cleaned.slice(0, 1500),
  };
}

async function analyze(filePath) {
  const data = new Uint8Array(fs.readFileSync(filePath));
  const doc = await getDocument({ data, useSystemFonts: true }).promise;
  const result = {
    file: path.basename(filePath),
    sizeBytes: data.length,
    pageCount: doc.numPages,
    pages: [],
  };

  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const viewport = page.getViewport({ scale: 1.0 });
    const textContent = await page.getTextContent();
    const strings = textContent.items.map((it) => ('str' in it ? it.str : '')).filter(Boolean);
    const rawText = strings.join(' ');
    const joinedLines = [];
    let line = '';
    for (const item of textContent.items) {
      if (!('str' in item)) continue;
      line += item.str;
      if (item.hasEOL) {
        joinedLines.push(line.trim());
        line = '';
      } else {
        line += ' ';
      }
    }
    if (line.trim()) joinedLines.push(line.trim());

    const ops = await page.getOperatorList();
    const hasImages = ops.fnArray.some((fn) => fn === 85 || fn === 82); // paintImageXObject / paintInlineImageXObject approx via names below
    // Better: inspect fn names
    const fnNames = ops.fnArray.map((fn) => (page.commonObjs && page.objs ? fn : fn));
    // Use pdfjs OPS enum indirectly: count image-like operators by stringifying ops
    const opCount = ops.fnArray.length;

    const meta = meaningfulText(rawText);
    result.pages.push({
      pageNumber: i,
      width: viewport.width,
      height: viewport.height,
      operatorCount: opCount,
      textItemCount: textContent.items.length,
      lineCount: joinedLines.length,
      ...meta,
      linesPreview: joinedLines.slice(0, 80),
    });
  }

  const meta = await doc.getMetadata().catch(() => null);
  result.metadata = meta?.info || null;
  return result;
}

for (const f of files) {
  console.log('='.repeat(80));
  console.log('Analyzing:', f);
  try {
    const r = await analyze(f);
    const out = path.join('out', path.basename(f) + '.json');
    fs.mkdirSync('out', { recursive: true });
    fs.writeFileSync(out, JSON.stringify(r, null, 2));
    console.log(JSON.stringify({
      file: r.file,
      sizeBytes: r.sizeBytes,
      pageCount: r.pageCount,
      metadata: r.metadata,
      pages: r.pages.map(p => ({
        pageNumber: p.pageNumber,
        width: p.width,
        height: p.height,
        textItemCount: p.textItemCount,
        charCount: p.charCount,
        alnumCount: p.alnumCount,
        isMeaningful: p.isMeaningful,
        lineCount: p.lineCount,
        preview: p.preview?.slice(0, 400),
      })),
    }, null, 2));
    console.log('Full dump:', out);
  } catch (e) {
    console.error('FAILED', f, e);
  }
}
