// handover.html 을 A4 PDF(Wuxi_OCR_인수인계서_v1.0.0.pdf)로 만든다. 쪽 아래에 제목과 쪽 번호.
//   npm i playwright-core   (처음 한 번, 크롬은 PC 에 깔린 것을 씀)
//   node docs/handover/build.cjs
// 크롬 위치가 다르면 CHROME 환경변수로 준다. 판을 올리면 아래 VERSION 과 handover.html 표지의 판을 같이 고친다.
const path = require('path');
const fs = require('fs');
const VERSION = 'v1.0.0';
let chromium;
try { ({ chromium } = require('playwright-core')); }
catch { ({ chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright')); }
const candidates = [process.env.CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium'].filter(Boolean);
const exe = candidates.find(p => fs.existsSync(p));
(async () => {
  const br = await chromium.launch(exe ? { executablePath: exe } : {});
  const page = await br.newPage();
  await page.goto('file:///' + path.resolve(__dirname, 'handover.html').replace(/\\/g, '/'), { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const out = path.resolve(__dirname, `Wuxi_OCR_인수인계서_${VERSION}.pdf`);
  await page.pdf({
    path: out, format: 'A4', printBackground: true, displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: '<div style="width:100%;font-size:7.5pt;color:#555;padding:0 15mm;display:flex;justify-content:space-between;font-family:Malgun Gothic,sans-serif">' +
      `<span>Wuxi OCR 인수인계서 ${VERSION}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
    margin: { top: '16mm', bottom: '18mm', left: '15mm', right: '15mm' },
  });
  console.log('pdf', out);
  await br.close();
})();
