// Browser check for the fms report: screenshots of every tab + console/network errors.
//
//   cd tests/visual && npm install            (once; installs puppeteer-core)
//   CHROME=$(command -v chromium) node visual.mjs [path/to/index.html] [out-dir]
//
// Without a report path, fms is run on examples/r-project first.
// Exit code 1 if any tab logged an error or a request failed.
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import puppeteer from 'puppeteer-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');
let report = process.argv[2];
if (!report) {
  const out = mkdtempSync(path.join(tmpdir(), 'fms-visual-'));
  execFileSync('python3', ['-m', 'findmysource', '--out', out],
    { cwd: path.join(repo, 'examples', 'r-project'), env: { ...process.env, PYTHONPATH: repo }, stdio: 'inherit' });
  report = path.join(out, 'index.html');
}
const shots = path.resolve(process.argv[3] || path.join(here, '..', 'screenshots'));
mkdirSync(shots, { recursive: true });
const chrome = process.env.CHROME || 'chromium';

const browser = await puppeteer.launch({
  executablePath: chrome, headless: true,
  args: ['--no-sandbox', '--allow-file-access-from-files'],
});
const problems = [];
const url = pathToFileURL(path.resolve(report)).href;

async function open(tab, { dark = false } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1500, height: 950 });
  if (dark) await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);
  const label = tab + (dark ? '-dark' : '');
  page.on('console', m => { if (m.type() === 'error') problems.push(`${label}: console: ${m.text()}`); });
  page.on('pageerror', e => problems.push(`${label}: page error: ${e.message}`));
  page.on('requestfailed', r => problems.push(`${label}: request failed: ${r.url()} (${r.failure()?.errorText})`));
  await page.goto(`${url}#${tab}`, { waitUntil: 'load' });
  return page;
}
const snap = (page, name) => page.screenshot({ path: path.join(shots, `${name}.png`) });

for (const tab of ['flow', 'timeline', 'matrix', 'table']) {
  const page = await open(tab);
  await snap(page, tab);
  if (tab === 'flow') {  // lineage highlight: click the first script box, then search
    // click the right-most box (a leaf output): everything not upstream of it must be dimmed
    const boxes = await page.$$('#fsvg .nd');
    const xs = await Promise.all(boxes.map(b => b.evaluate(e => e.getBoundingClientRect().x)));
    const node = boxes[xs.indexOf(Math.max(...xs))];
    if (!node) problems.push('flow: no nodes drawn');
    else {
      const total = boxes.length;
      const { width } = await page.$eval('#fsvg', e => e.getBoundingClientRect());
      const card = await page.$eval('#fcard', e => e.clientWidth);
      if (width > card + 1) problems.push(`flow: map (${width}px) wider than its card (${card}px) in fit mode`);
      await node.click();
      const dimmed = await page.$$eval('#fsvg .nd.dim', n => n.length);
      console.log(`flow: ${total} boxes, ${dimmed} dimmed after clicking the right-most one`);
      if (!dimmed) problems.push('flow: clicking a leaf dimmed nothing');
      await snap(page, 'flow-click');
      await node.hover();
      const tip = await page.$eval('#tip', t => t.style.display === 'block' && t.textContent);
      if (!tip) problems.push('flow: hovering a box shows no tooltip');
    }
  }
  await page.close();
}

// network: vis-network comes from a CDN; wait for its canvas or the offline message
{
  const page = await open('network');
  const state = await page.waitForFunction(() =>
    document.querySelector('#netbox canvas') ? 'canvas' :
    document.querySelector('#netbox .empty') ? 'offline message' : null, { timeout: 20000 })
    .then(h => h.jsonValue()).catch(() => 'nothing after 20 s');
  console.log(`network: ${state}`);
  if (state !== 'canvas') problems.push(`network: ${state}`);
  await new Promise(r => setTimeout(r, 2500));  // let the layout settle before the screenshot
  await snap(page, 'network');
  await page.close();
}

{ const page = await open('flow', { dark: true }); await snap(page, 'flow-dark'); await page.close(); }
await browser.close();

console.log(`screenshots: ${shots}`);
if (problems.length) { console.log('PROBLEMS:\n  ' + problems.join('\n  ')); process.exit(1); }
console.log('no errors');
