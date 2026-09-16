// Render captured PTY bytes with xterm.js, then screenshot that terminal.
// Development-only dependencies: playwright and @xterm/xterm. No app dependency.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=__dirname;
const xterm=path.dirname(require.resolve('@xterm/xterm/package.json'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1220,height:750},deviceScaleFactor:2});
  await page.setContent(`<html><head><style>
  *{box-sizing:border-box}body{margin:0;background:#080e17;padding:36px;color:#e8eef6;font:13px monospace}
  .window{background:#101824;border:1px solid #293748;border-radius:14px;overflow:hidden;box-shadow:0 22px 70px #0008}
  .bar{height:44px;border-bottom:1px solid #253041;background:#162130;display:flex;align-items:center;gap:9px;padding:0 18px}
  .dot{height:10px;width:10px;border-radius:100%;background:#43536a}.name{margin-left:16px;color:#bac8db}.tag{margin-left:auto;color:#6fe8bf;font-size:11px}
  #terminal{padding:14px 10px 14px 8px}.footer{margin-top:13px;color:#71839c;text-align:center;font-size:11px}
  </style></head><body><div class="window"><div class="bar"><i class="dot"></i><i class="dot"></i><i class="dot"></i><span class="name">diskpick — interactive storage cleanup</span><span class="tag">ACTUAL CLI OUTPUT · DEMO DATA</span></div><div id="terminal"></div></div><div class="footer">Captured from a real pseudo-terminal. Demo mode never scans or deletes personal files.</div></body></html>`);
  await page.addStyleTag({path:path.join(xterm,'css/xterm.css')});
  await page.addScriptTag({path:path.join(xterm,'lib/xterm.js')});
  for (const name of ['picker','result']) {
   const raw=fs.readFileSync(path.join(root,name+'.ansi'),'utf8');
   await page.evaluate(async data=>{
    if(window.term)window.term.dispose();
    window.term=new Terminal({cols:112,rows:29,fontSize:14,fontFamily:'Menlo, monospace',lineHeight:1.32,disableStdin:true,
      theme:{background:'#101824',foreground:'#e8eef6',cursor:'#6fe8bf'},cursorBlink:false});
    term.open(document.getElementById('terminal'));
    await new Promise(resolve=>term.write(data,resolve));
   },raw);
   await page.screenshot({path:path.join(root,name+'.png'),fullPage:true});
  }
 } finally {await browser.close();}
})();
