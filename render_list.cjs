// Render actual PTY output with xterm; no duplicate product UI.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const xterm = path.dirname(require.resolve('@xterm/xterm/package.json'));
const root = path.join(__dirname, 'evidence/list-ui');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const name of ['list', 'selected', 'review', 'add', 'options', 'narrow', 'details-narrow', 'storage', 'storage-depth', 'storage-zoom', 'storage-effort', 'storage-narrow']) {
      const {cols, rows} = JSON.parse(fs.readFileSync(path.join(root, name + '.json')));
      for (const dark of (['list', 'storage'].includes(name) ? [false, true] : [false])) {
        const page = await browser.newPage({viewport: {width: Math.ceil(cols * 8.44 + 40), height: rows * 20 + 54}, deviceScaleFactor: 2});
        const canvas = dark ? '#292623' : '#faf6f1';
        const ink = dark ? '#e8dfd5' : '#3d342d';
        await page.setContent(`<style>body{margin:0;background:${canvas};color:${ink}}#terminal{padding:16px 20px 0}.caption{font:11px system-ui;padding:8px 22px;border-top:1px solid ${dark ? '#514a43' : '#d3c8bc'}} </style><div id="terminal"></div><div class="caption">DiskBoard · Actual terminal output · Synthetic demo data</div>`);
        await page.addStyleTag({path: path.join(xterm, 'css/xterm.css')});
        await page.addScriptTag({path: path.join(xterm, 'lib/xterm.js')});
        await page.evaluate(async ({cols, rows, canvas, ink, data}) => {
          const term = new Terminal({cols, rows, fontSize: 14, fontFamily: 'Menlo, monospace', lineHeight: 1.25,
            theme: {background: canvas, foreground: ink, cursor: ink}, disableStdin: true, cursorBlink: false, scrollback: 0});
          term.open(document.getElementById('terminal'));
          await new Promise(resolve => term.write(data, resolve));
          window.lines = Array.from({length: rows}, (_, i) => term.buffer.active.getLine(i)?.translateToString(true) || '');
        }, {cols, rows, canvas, ink, data: fs.readFileSync(path.join(root, name + '.ansi'), 'utf8')});
        const label = name + (dark ? '-dark' : '');
        fs.writeFileSync(path.join(root, label + '.txt'), (await page.evaluate(() => window.lines)).join('\n'));
        await page.screenshot({path: path.join(root, label + '.png'), fullPage: true});
        if (name === 'storage' && !dark) fs.copyFileSync(path.join(root, label + '.png'), path.join(__dirname, 'home.png'));
        await page.close();
      }
    }
  } finally { await browser.close(); }
})();
