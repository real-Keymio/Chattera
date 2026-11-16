// Renders the two user guides (docs/user-guide/*.html) to PDF with the installed Microsoft Edge.
//   npm install --no-save playwright-core     (once, anywhere on NODE_PATH; no browser download is needed)
//   node scripts/build_guides.js [output folder]          default: docs/user-guide/dist
// Chinese text is embedded as subsetted system fonts (Microsoft YaHei), so it stays searchable and copyable.
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright-core');

const root = path.resolve(__dirname, '..');
const out = path.resolve(process.argv[2] || path.join(root, 'docs', 'user-guide', 'dist'));
const guides = [['01_start.html', '01_先看这个.pdf'], ['02_full.html', '02_Chattera完整使用指南.pdf']];

(async () => {
  require('fs').mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage();
  for (const [source, target] of guides) {
    await page.goto(pathToFileURL(path.join(root, 'docs', 'user-guide', source)).href, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.pdf({ path: path.join(out, target), format: 'A4', printBackground: true, preferCSSPageSize: true,
      displayHeaderFooter: true, headerTemplate: '<span></span>',
      footerTemplate: '<div style="width:100%;font-size:8px;color:#8a92a6;text-align:center;font-family:Microsoft YaHei">Chattera · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
      margin: { top: '14mm', bottom: '16mm', left: '15mm', right: '15mm' } });
    console.log('wrote', target);
  }
  await browser.close();
})();
