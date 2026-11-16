// A crew account with no studio (the main admin) sends a flag from the board, in a real browser (headless Chrome over the DevTools protocol, Node built-ins
// only). Before 16 November the page showed CTFd's bare "You don't have the permission to access the requested resource"; it now says why the flag was not
// checked and what to do, and the programme does not turn solved. Run by test_crew_flag_browser.py, which seeds a programme on air.
//   env: L3MON_BASE, L3MON_COOKIE (the organiser's session), L3MON_PROGRAMME (the challenge's name), L3MON_FLAG (its right flag), L3MON_SHOTS (optional), CHROME (if not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const PROGRAMME = process.env.L3MON_PROGRAMME;
const FLAG = process.env.L3MON_FLAG;
const { check, finish } = checker();
const chrome = await launch();
try {
  const page = await openTab(chrome, { base: BASE, cookie: process.env.L3MON_COOKIE, shots: process.env.L3MON_SHOTS, width: 1366, height: 900 });
  const set = (selector, value) => page.run(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.focus(); e.value = ${JSON.stringify(value)}; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
  const reply = () => page.run('document.querySelector("[data-pn-reply]").innerText');
  await page.goto(`${BASE}/board`);
  await sleep(3000);
  await page.run(`(() => { const tile = Array.from(document.querySelectorAll('#tiles li.tile')).find((t) => t.innerText.includes(${JSON.stringify(PROGRAMME)})); tile.querySelector('a').click(); return true; })()`);
  await sleep(2500);
  check('the crew open the programme on the board', (await page.run('document.getElementById("pn-title").innerText')).includes(PROGRAMME));
  for (const [what, flag] of [['a wrong flag', 'L3m0n' + '{not_the_flag}'], ['the right flag', FLAG]]) {
    await set('#pn-flag-in', flag);
    await page.run(`document.querySelector('[data-pn-send]').click()`);
    await sleep(2500);
    let text = await reply();
    if (/CHECK THE FORMAT/i.test(text)) { // the page nudges once when a flag does not look like L3m0n{...}; Send again sends it anyway
      await page.run(`document.querySelector('[data-pn-send]').click()`);
      await sleep(2500);
      text = await reply();
    }
    check(`${what} from a crew account with no studio is told why it was not checked`, /crew accounts have none/.test(text) && /player account that has a studio/.test(text) && /Preview/.test(text), text);
    check(`${what}: it is not shown as solved and the CTFd wording is gone`, /^RESTRICTED\./.test(text.trim()) && !/SIGNAL LOCKED/i.test(text) && !/permission to access the requested resource/i.test(text), text);
  }
  await page.shot('crew-flag-1-reply.png');
  check('no script error', page.log.errors.length === 0, JSON.stringify(page.log.errors));
} finally {
  await chrome.close();
}
finish();
