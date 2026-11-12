import fs from 'fs';
import path from 'path';
import { createRequire } from 'module';
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs';

const require = createRequire(import.meta.url);
const { createCanvas } = require('canvas');

const filePath = path.resolve('..', 'HYD_OtherType_2026-2027_10250.pdf');
const data = new Uint8Array(fs.readFileSync(filePath));
console.log('bytes', data.length);

const doc = await getDocument({ data, useSystemFonts: true }).promise;
fs.mkdirSync('out', { recursive: true });

for (let i = 1; i <= doc.numPages; i++) {
  const page = await doc.getPage(i);
  const scale = 2.0;
  const viewport = page.getViewport({ scale });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport }).promise;
  const out = path.join('out', `other_page_${i}.png`);
  fs.writeFileSync(out, canvas.toBuffer('image/png'));
  console.log('wrote', out, canvas.width, 'x', canvas.height);
}
