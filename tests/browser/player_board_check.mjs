// A player's whole turn on the board, in a real browser (headless Chrome over the DevTools protocol, Node built-ins only): sign in, set up a studio, see a
// programme the crew put on air, open it, send a wrong flag and then the right one, and see the TRP. Run by test_player_board_browser.py, which makes the
// player (verified, no studio), the challenge and the plan, and passes their names in the environment.
//   env: L3MON_BASE, L3MON_PLAYER, L3MON_PASSWORD, L3MON_PROGRAMME (the challenge's name), L3MON_FLAG, L3MON_VALUE, L3MON_SHOTS (optional), CHROME (if not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const { L3MON_PLAYER: PLAYER, L3MON_PASSWORD: PASSWORD, L3MON_PROGRAMME: PROGRAMME, L3MON_FLAG: FLAG } = process.env;
const VALUE = Number(process.env.L3MON_VALUE);
const { check, finish } = checker();

const chrome = await launch();
try {
  const page = await openTab(chrome, { base: BASE, shots: process.env.L3MON_SHOTS, width: 1366, height: 900 });
  const where = () => page.run('location.pathname + location.search');
  const text = () => page.run('document.body.innerText');
  const set = (selector, value) => page.run(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.focus(); e.value = ${JSON.stringify(value)}; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
  const open = async (path) => { await page.goto(`${BASE}${path}`); await sleep(1500); };

  // ---- sign in with the real form ----
  await open('/login');
  await set('input[name=name]', PLAYER);
  await set('input[name=password]', PASSWORD);
  await page.run(`document.querySelector('form [type=submit], form [data-submit]').click()`);
  await sleep(3000);
  const me = await page.run(`fetch('/api/v1/users/me', { credentials: 'same-origin', redirect: 'manual' }).then((r) => r.status)`);
  check('the player signs in with the real form', me === 200, String(me));

  // ---- a studio ----
  await open('/teams/new');
  check('a player with no studio is asked to set one up', /studio/i.test(await text()), (await where()));
  await page.run(`(() => { const r = document.querySelector('input[name=mode][value=create]'); if (r) r.click(); return true; })()`);
  await set('input[name=name]', `${PLAYER} studio`);
  await page.run(`(() => { const c = document.querySelector('select[name=country]'); const o = Array.from(c.options).find((x) => /^india$/i.test(x.text.trim())); c.value = o ? o.value : c.options[1].value; c.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
  await page.shot('player-1-studio.png');
  await page.run(`Array.from(document.querySelectorAll('button')).find((b) => /finish setup/i.test(b.innerText)).click()`);
  await sleep(3500);
  const team = await page.run(`fetch('/api/v1/teams/me', { credentials: 'same-origin' }).then((r) => r.json()).then((j) => j.data && j.data.name)`);
  check('the studio exists and is the player\'s', team === `${PLAYER} studio`, String(team));

  // ---- the board ----
  await open('/board');
  await sleep(1500);
  const tiles = await page.run(`Array.from(document.querySelectorAll('#tiles li.tile')).map((t) => t.innerText.replace(/\\s+/g, ' ').trim())`);
  check('the programme the crew put on air is on the board, with its value', tiles.some((t) => t.includes(PROGRAMME) && t.includes(String(VALUE))), JSON.stringify(tiles));
  await page.shot('player-2-board.png');

  // ---- the panel and the flag ----
  await page.run(`(() => { const tile = Array.from(document.querySelectorAll('#tiles li.tile')).find((t) => t.innerText.includes(${JSON.stringify(PROGRAMME)})); tile.querySelector('a').click(); return true; })()`);
  await sleep(2500);
  check('opening the tile opens the panel for that programme', (await page.run('document.getElementById("pn-title").innerText')).includes(PROGRAMME), await where());
  await set('#pn-flag-in', 'L3m0n' + '{not_the_flag}');
  await page.run(`document.querySelector('[data-pn-send]').click()`);
  await sleep(2500);
  const wrong = await page.run('document.querySelector("[data-pn-reply]").innerText');
  check('a wrong flag is answered plainly and costs nothing', /NO SIGNAL|not the flag/i.test(wrong), wrong);
  await set('#pn-flag-in', FLAG);
  await page.run(`document.querySelector('[data-pn-send]').click()`);
  await sleep(2500);
  let right = await page.run('document.querySelector("[data-pn-reply]").innerText');
  if (/CHECK THE FORMAT/i.test(right)) { // the page nudges once when a flag does not look like L3m0n{...}; pressing Send again sends it anyway
    await page.run(`document.querySelector('[data-pn-send]').click()`);
    await sleep(2500);
    right = await page.run('document.querySelector("[data-pn-reply]").innerText');
  }
  check('the right flag locks the signal and shows the TRP', /SIGNAL LOCKED/i.test(right) && right.includes(String(VALUE)), right);
  await page.shot('player-3-solved.png');

  // ---- what the platform holds ----
  const board = await page.run(`fetch('/api/v1/l3mon/board', { credentials: 'same-origin' }).then((r) => r.json()).then((j) => ({ score: j.data.team.score, place: j.data.team.place, of: j.data.team.of, solved: j.data.programmes.filter((p) => p.solved_by_me).length }))`);
  check('the studio holds the TRP, is first of one ranked studio, and the programme is solved', board.score === VALUE && board.place === 1 && board.of === 1 && board.solved === 1, JSON.stringify(board));
  await open('/scoreboard');
  check('the scoreboard shows the studio', (await text()).includes(`${PLAYER} studio`) && (await text()).includes(String(VALUE)), (await text()).slice(0, 200));

  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
  check('nothing was asked of any other host', page.log.urls.every((u) => u.startsWith(BASE) || u.startsWith('data:')), JSON.stringify(page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:'))));
} finally {
  await chrome.close();
}
finish();
