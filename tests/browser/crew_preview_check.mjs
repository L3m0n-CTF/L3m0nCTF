// The crew's view BEFORE the broadcast starts, in a real browser: the Release control page says plainly that players see nothing yet, and the board shows the crew
// the programme that is on air as a preview (players see "not on air yet"). Run by test_crew_preview_browser.py, which seeds a released programme.
//   env: L3MON_BASE, L3MON_COOKIE (the organiser's session), L3MON_PROGRAMME (the challenge's name), L3MON_SHOTS (optional), CHROME (if not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const { check, finish } = checker();
const chrome = await launch();
try {
  const page = await openTab(chrome, { base: BASE, cookie: process.env.L3MON_COOKIE, shots: process.env.L3MON_SHOTS, width: 1366, height: 900 });
  await page.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2500);
  const text = () => page.run('document.getElementById("release-app").innerText');
  check('the page says the broadcast has not started and players see nothing yet', /The broadcast has not started/.test(await text()) && /Players see nothing on the board until then/.test(await text()));
  check('the released programme says it is on air but waiting, not that players see it',
    /on air, waiting for the broadcast to start/.test(await text()) && !/players see it/.test(await text()), (await text()).slice(0, 400));
  check('the page points to the rehearsal guide', /docs\/runbooks\/15-rehearsal\.md/.test(await text()));
  await page.shot('preview-1-release.png');

  await page.goto(`${BASE}/board`);
  await sleep(3000);
  const tiles = await page.run(`Array.from(document.querySelectorAll('#tiles li.tile')).map((t) => t.innerText.replace(/\s+/g, ' ').trim())`);
  check('the board shows the crew the programme that is on air', tiles.some((t) => t.includes(process.env.L3MON_PROGRAMME)), JSON.stringify(tiles));
  check('and not the "not on air yet" card', !/NOT ON AIR YET/i.test(await page.run('document.body.innerText')));
  await page.shot('preview-2-board.png');

  // a player (a visitor here) is told it is not on air: the API says "before" for anybody who is not the crew
  const visitor = await page.run(`fetch('/api/v1/l3mon/board', { credentials: 'omit' }).then((r) => r.status)`);
  check('a visitor gets no board at all (sign-in first), the crew\'s own answer is not shared', visitor === 401 || visitor === 302 || visitor === 403, String(visitor));
  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
} finally {
  await chrome.close();
}
finish();
