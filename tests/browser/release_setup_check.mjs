// The crew's "Set up the board" forms in a real browser (headless Chrome over the DevTools protocol, Node built-ins only).
// Run by test_release_setup_browser.py, which makes three challenges that are on no channel (and leaves the plan empty, as on a fresh platform),
// signs in as the organiser and passes the session cookie in L3MON_COOKIE.
//   env: L3MON_BASE (default http://localhost:8080), L3MON_COOKIE, L3MON_SHOTS (a folder for screenshots, optional), CHROME (if not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const COOKIE = process.env.L3MON_COOKIE;
const SHOTS = process.env.L3MON_SHOTS;
const { check, finish } = checker();

const chrome = await launch();
try {
  const page = await openTab(chrome, { base: BASE, cookie: COOKIE, shots: SHOTS });
  await page.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2500);
  const text = () => page.run('document.getElementById("release-app").innerText');
  const form = (name) => `document.querySelector('form[data-form="${name}"]')`;
  // set a field the way a person does (the page listens to input and change), then submit the form with its own button
  const set = (name, field, value) => page.run(`(() => { const e = ${form(name)}.elements[${JSON.stringify(field)}]; e.value = ${JSON.stringify(value)}; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return e.value; })()`);
  const get = (name, field) => page.run(`${form(name)}.elements[${JSON.stringify(field)}].value`);
  const submit = async (name) => { await page.run(`${form(name)}.querySelector('button[type=submit]').click()`); await sleep(1800); };
  const note = () => page.run('document.querySelector(".rc-setup-note").textContent');
  // choose an entry of a list by the words it shows, as a person does (the development stack may hold other challenges too)
  const pick = (name, field, words) => page.run(`(() => { const s = ${form(name)}.elements[${JSON.stringify(field)}]; const o = Array.from(s.options).find((x) => x.textContent.includes(${JSON.stringify(words)})); s.value = o.value; s.dispatchEvent(new Event('change', { bubbles: true })); return s.value; })()`);
  const options = (name, field) => page.run(`Array.from(${form(name)}.elements[${JSON.stringify(field)}].options).map((o) => o.textContent)`);

  // ---- a fresh platform: no channel, three challenges waiting ----
  check('the forms are open when there is no channel yet', await page.run('document.querySelector("details.rc-setup").open'));
  check('the page says there are no channels and where to add one', /There are no channels yet\. Add the first one under "Set up the board"/.test(await text()));
  const waiting = await options('programme', 'challenge');
  check('the challenge list holds the three challenges, each with category and value',
    ['Wombat Walk (Web, 100 TRP)', 'Numbat Night (Crypto, 200 TRP)', 'Quokka Quiz (Misc, 300 TRP)'].every((label) => waiting.some((o) => o.includes(label))), JSON.stringify(waiting));
  check('the challenge form waits for a channel', (await options('programme', 'channel'))[0].includes('Add a channel first') && await page.run(`${form('programme')}.querySelector('button[type=submit]').disabled`));
  await page.shot('setup-1-empty.png');

  // ---- 1. a channel ----
  await set('channel', 'name', 'Test Street');
  check('the short address follows the name', (await get('channel', 'slug')) === 'test-street', await get('channel', 'slug'));
  check('the number on the dial starts at 1', (await get('channel', 'position')) === '1');
  await submit('channel');
  check('the channel is added, off air, and the page says what comes next', /Channel "Test Street" added\. It is off air\./.test(await note()), await note());
  check('the channel is drawn', await page.run('document.querySelectorAll(".rc-channel").length') === 1 && /CH 1 · Test Street/.test(await text()));
  check('the name box is empty again and the next channel would be number 2', (await get('channel', 'name')) === '' && (await get('channel', 'position')) === '2');
  check('the challenge form knows the channel now', (await options('programme', 'channel'))[0] === 'CH 1 · Test Street' && await page.run(`!${form('programme')}.querySelector('button[type=submit]').disabled`));

  // ---- 2. a challenge on the channel ----
  await pick('programme', 'challenge', 'Wombat Walk');
  check('a challenge is chosen and its short name, number and place are filled in', (await get('programme', 'slug')) === 'wombat_walk' && (await get('programme', 'number')) === '101' && (await get('programme', 'cell')) === '0',
    [await get('programme', 'slug'), await get('programme', 'number'), await get('programme', 'cell')].join(' '));
  await submit('programme');
  check('the challenge is on the channel, off air', /"Wombat Walk" is on channel "Test Street" as programme 101\. It is off air/.test(await note()), await note());
  check('its row says withheld and off air', /wombat_walk/.test(await text()) && /withheld/.test(await text()) && /off air/.test(await text()));
  await pick('programme', 'challenge', 'Numbat Night');
  check('the next challenge gets the next number and place', (await get('programme', 'slug')) === 'numbat_night' && (await get('programme', 'number')) === '102' && (await get('programme', 'cell')) === '1',
    [await get('programme', 'slug'), await get('programme', 'number'), await get('programme', 'cell')].join(' '));

  // a number that is taken is refused in plain words and nothing is added
  await set('programme', 'number', '101');
  await submit('programme');
  check('a programme number used twice is refused and says why', await page.run('document.querySelector(".rc-setup-note").classList.contains("rc-error")') && /number/.test(await note()), await note());
  check('and nothing was added', (await page.run('document.querySelectorAll(".rc-channel tbody tr").length')) === 1);
  await set('programme', 'number', '102');
  await submit('programme');
  check('with a free number it works', /programme 102/.test(await note()) && (await page.run('document.querySelectorAll(".rc-channel tbody tr").length')) === 2, await note());

  // ---- 3. a whole plan, and a mistake in it ----
  await set('json', 'plan', '{"channels": [');
  await submit('json');
  check('text that is not JSON is refused in plain words', /not valid JSON/.test(await note()), await note());
  await set('json', 'plan', JSON.stringify({ channels: [{ slug: 'json-square', name: 'Json Square', position: 5 }], programmes: [] }));
  await submit('json');
  check('a pasted plan is loaded and its channel appears', /plan was loaded/.test(await note()) && /CH 5 · Json Square/.test(await text()), await note());
  await page.shot('setup-2-built.png');

  // ---- putting it on air with the buttons the page already had ----
  await page.run(`(() => { const head = Array.from(document.querySelectorAll('.rc-channel > header')).find((h) => h.textContent.includes('Test Street')); Array.from(head.querySelectorAll('button')).find((b) => b.textContent.startsWith('Release now')).click(); })()`);
  await sleep(1500);
  await page.run(`(() => { const row = Array.from(document.querySelectorAll('tr')).find((r) => r.children[1] && r.children[1].textContent === 'wombat_walk'); Array.from(row.querySelectorAll('button')).find((b) => b.textContent.startsWith('Release now')).click(); })()`);
  await sleep(1500);
  const row = await page.run(`(Array.from(document.querySelectorAll('tr')).find((r) => r.children[1] && r.children[1].textContent === 'wombat_walk') || {}).innerText || ''`);
  check('the channel and the first programme can be put on air from the same page', /released/.test(row) && /players see it|on air/.test(row), row);

  // ---- the way back: a programme off the board, an empty channel removed ----
  const loseRow = (slug) => page.run(`(() => { const row = Array.from(document.querySelectorAll('tr')).find((r) => r.children[1] && r.children[1].textContent === ${JSON.stringify(slug)}); Array.from(row.querySelectorAll('button')).find((b) => b.textContent.startsWith('Take off the board')).click(); })()`);
  let before = page.log.dialogs || 0;
  await loseRow('numbat_night');
  await sleep(1800);
  const hasRow = (slug) => page.run(`Array.from(document.querySelectorAll('tr')).some((r) => r.children[1] && r.children[1].textContent === ${JSON.stringify(slug)})`);
  const hasChannel = (name) => page.run(`Array.from(document.querySelectorAll('.rc-channel > header')).some((h) => h.textContent.includes(${JSON.stringify(name)}))`);
  const status = () => page.run('document.getElementById("release-status").textContent');
  check('Take off the board asks first, then the programme is gone and its challenge waits for a channel again',
    (page.log.dialogs || 0) === before + 1 && !(await hasRow('numbat_night')) && (await options('programme', 'challenge')).some((o) => o.includes('Numbat Night')),
    JSON.stringify({ dialogs: (page.log.dialogs || 0) - before, status: await status(), row: await hasRow('numbat_night') }));
  before = page.log.dialogs || 0;
  await page.run(`(() => { const head = Array.from(document.querySelectorAll('.rc-channel > header')).find((h) => h.textContent.includes('Json Square')); Array.from(head.querySelectorAll('button')).find((b) => b.textContent.startsWith('Remove this empty channel')).click(); })()`);
  await sleep(1800);
  check('an empty channel can be removed (asks first); a channel that holds a programme has no such button',
    (page.log.dialogs || 0) === before + 1 && !(await hasChannel('Json Square')) && (await hasChannel('Test Street')) &&
    await page.run(`Array.from(document.querySelectorAll('.rc-channel > header')).filter((h) => h.textContent.includes('Test Street')).every((h) => !Array.from(h.querySelectorAll('button')).some((b) => b.textContent.startsWith('Remove this empty channel')))`));
  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
  check('nothing was asked of any other host', page.log.urls.every((u) => u.startsWith(BASE) || u.startsWith('data:')), JSON.stringify(page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:'))));

  // ---- a phone ----
  const phone = await openTab(chrome, { base: BASE, cookie: COOKIE, shots: SHOTS, width: 390, height: 844, mobile: true });
  await phone.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2500);
  await phone.run('document.querySelector("details.rc-setup").open = true');
  const spill = await phone.run('[document.documentElement.scrollWidth, window.innerWidth]');
  check('at phone width nothing spills sideways', spill[0] <= spill[1] + 1, JSON.stringify(spill));
  await phone.shot('setup-3-phone.png');
} finally {
  await chrome.close();
}
finish();
