/* Release control: the crew's page.
 * Reads and writes only through /api/v1/l3mon/admin/release. Everything on the page is drawn with text nodes, never as markup,
 * so a channel or programme whose name contains markup stays plain text. Times are typed and shown in India (IST, UTC+5:30). */
(function () {
  'use strict';

  var IST_OFFSET_MINUTES = 330;
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var REFRESH_MS = 15000;

  var app = document.getElementById('release-app');
  if (!app) return;
  var api = app.getAttribute('data-api');
  var planApi = app.getAttribute('data-plan-api');
  var nonce = (window.init && window.init.csrfNonce) || '';
  var statusLine = document.getElementById('release-status');
  var plan = null;
  var busy = false;
  var editing = null; // 'kind:id' of the entry whose schedule box is open
  var shell = null; // the parts of the page that are built once and kept: the summary, the reason box and the buttons above the plan

  function node(tag, props, kids) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === 'text') n.textContent = props[k];
      else if (k === 'class') n.className = props[k];
      else n.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (kid) {
      if (kid !== null && kid !== undefined && kid !== false) n.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return n;
  }

  function say(text, isError) {
    statusLine.textContent = text;
    statusLine.className = isError ? 'rc-error' : '';
  }

  function istText(epoch) {
    var d = new Date((epoch + IST_OFFSET_MINUTES * 60) * 1000);
    var hh = ('0' + d.getUTCHours()).slice(-2);
    var mm = ('0' + d.getUTCMinutes()).slice(-2);
    return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear() + ', ' + hh + ':' + mm + ' IST';
  }

  function istInputValue(epoch) {
    return new Date((epoch + IST_OFFSET_MINUTES * 60) * 1000).toISOString().slice(0, 16);
  }

  function istToEpoch(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || '');
    if (!m) return null;
    return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000) - IST_OFFSET_MINUTES * 60;
  }

  function problems(body) {
    if (!body || !body.errors) return 'The request was refused.';
    return Object.keys(body.errors).map(function (field) { return field + ': ' + body.errors[field].join(' '); }).join(' | ');
  }

  function send(method, payload, url) {
    var options = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'CSRF-Token': nonce } };
    if (payload) options.body = JSON.stringify(payload);
    return fetch(url || api, options).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (body) {
        if (!response.ok || !body || body.success !== true) throw new Error(problems(body));
        return body.data;
      });
    });
  }

  function change(changes) {
    if (busy) return;
    busy = true;
    editing = null;
    say('Saving…', false);
    send('PUT', { changes: changes, reason: shell ? shell.reasonBox.value : '' }).then(function (data) {
      plan = data;
      if (shell) shell.reasonBox.value = ''; // a reason belongs to the change it was typed for, never to the next click
      var r = data.result;
      say(r.changed === 0 ? 'Nothing needed changing.' : r.changed + ' changed. ' + r.shown + ' now on air, ' + r.hidden + ' pulled back.', false);
      busy = false;
      render();
    }).catch(function (error) {
      busy = false;
      say(error.message, true);
      render();
    });
  }

  function remove(kind, item) {
    if (busy) return;
    var text = kind === 'programme'
      ? 'Take "' + item.name + '" off the board? The challenge stays in CTFd, on no channel, and players stop seeing it.'
      : 'Remove the channel "' + item.name + '"? It has no programmes.';
    if (!window.confirm(text)) return;
    busy = true;
    editing = null;
    say('Removing…', false);
    send('DELETE', {}, planApi.replace(/programmes$/, kind === 'programme' ? 'programmes/' : 'channels/') + item.id).then(function (data) {
      plan = data;
      busy = false;
      say(kind === 'programme' ? '"' + item.name + '" is off the board.' : 'Channel "' + item.name + '" removed.', false);
      render();
    }).catch(function (error) {
      busy = false;
      say(friendly(error.message), true);
      render();
    });
  }

  function load() {
    if (busy || editing) return;
    send('GET').then(function (data) {
      plan = data;
      if (!statusLine.className) say('', false);
      render();
    }).catch(function (error) { say(error.message, true); });
  }

  function chip(entry) {
    var label = entry.state === 'scheduled' ? 'scheduled for ' + entry.at_ist : entry.state;
    var kind = entry.state === 'released' ? 'badge-success' : entry.state === 'scheduled' ? 'badge-info' : 'badge-secondary';
    return node('span', { 'class': 'badge ' + kind, text: label });
  }

  function actions(kind, item) {
    var key = kind + ':' + item.id;
    var box = node('div', { 'class': 'rc-actions' });
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-success', text: 'Release now' }));
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-secondary', text: 'Withhold' }));
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-info', text: 'Schedule…' }));
    var buttons = box.querySelectorAll('button');
    buttons[0].addEventListener('click', function () {
      if (!window.confirm('Put "' + item.name + '" on air now? Players will see it at once, and it cannot be unseen.')) return;
      change([{ kind: kind, id: item.id, mode: 'release' }]);
    });
    buttons[1].addEventListener('click', function () { change([{ kind: kind, id: item.id, mode: 'withhold' }]); });
    buttons[2].addEventListener('click', function () { editing = key; render(); });
    if (kind === 'programme') {
      var off = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-danger', text: 'Take off the board' });
      off.addEventListener('click', function () { remove('programme', item); });
      box.appendChild(off);
    }
    if (editing === key) {
      var input = node('input', { type: 'datetime-local', 'class': 'form-control form-control-sm', 'aria-label': 'Release time in India (IST)', value: istInputValue(plan.now + 600) });
      var set = node('button', { type: 'button', 'class': 'btn btn-sm btn-primary', text: 'Set' });
      var cancel = node('button', { type: 'button', 'class': 'btn btn-sm btn-link', text: 'Cancel' });
      set.addEventListener('click', function () {
        var at = istToEpoch(input.value);
        if (at === null || at <= plan.now) { say('Choose a time in India that is still to come.', true); return; }
        change([{ kind: kind, id: item.id, mode: 'schedule', at: at }]);
      });
      cancel.addEventListener('click', function () { editing = null; render(); });
      box.appendChild(node('span', { 'class': 'rc-when' }, [input, set, cancel]));
    }
    return box;
  }

  function channelBlock(ch) {
    var rows = ch.programmes.map(function (p) {
      var cls = p.on_air ? 'rc-onair' : 'rc-off';
      return node('tr', {}, [
        node('td', { 'data-label': 'No.', text: String(p.number) }),
        node('td', { 'data-label': 'Slug', text: p.slug }),
        node('td', { 'data-label': 'Challenge', text: p.name }),
        node('td', { 'data-label': 'Setting' }, [chip(p)]),
        node('td', { 'data-label': 'Players', 'class': cls, text: p.visible ? 'players see it' : p.on_air ? 'on air (CTFd is catching up)' : 'off air' }),
        node('td', { 'data-label': 'Change' }, [actions('programme', p)])
      ]);
    });
    var table = node('table', { 'class': 'table table-sm' }, [
      node('thead', {}, [node('tr', {}, ['No.', 'Slug', 'Challenge', 'Setting', 'Players', 'Change'].map(function (h) { return node('th', { text: h }); }))]),
      node('tbody', {}, rows)
    ]);
    var head = node('header', {}, [
      node('h2', { text: 'CH ' + ch.position + ' · ' + ch.name + (ch.kind === 'sponsored' ? ' (sponsored by ' + (ch.sponsor_name || '?') + ')' : '') }),
      chip(ch),
      node('span', { 'class': ch.on_air ? 'rc-onair' : 'rc-off', text: ch.on_air ? 'channel on air' : 'channel off air' }),
      actions('channel', ch)
    ]);
    if (!ch.programmes.length) {
      var gone = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-danger', text: 'Remove this empty channel' });
      gone.addEventListener('click', function () { remove('channel', ch); });
      head.appendChild(gone);
    }
    return node('section', { 'class': 'rc-channel' }, [head, ch.programmes.length ? table : node('p', { 'class': 'p-3 mb-0', text: 'No programmes on this channel yet.' })]);
  }

  // ---- setting up the board: a channel, then a challenge on it (the plan, without writing JSON) ------------------------------------------------------------
  // A challenge made in CTFd is on no channel, so the board never draws it. These forms are the crew's way of making a channel and putting a challenge on it.
  // They write through PUT /api/v1/l3mon/admin/programmes, the same call the author kit uses; whatever they add starts OFF AIR (the buttons below put it on).
  var DIFFICULTY_WORDS = [['warmup', 'Warm-up'], ['easy', 'Easy'], ['medium', 'Medium'], ['hard', 'Hard'], ['insane', 'Insane']];
  var DELIVERY_WORDS = [
    ['static_shared', 'The same flag for every studio (most challenges)'],
    ['static_per_participant', 'A different flag for each studio'],
    ['live_single', 'A live server for each studio'],
    ['live_multi', 'Several live servers for each studio']
  ];
  var setup = null; // the forms: built once, so that what is being typed survives the plan being read again

  function slugify(text, sep, max) {
    var out = String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, sep);
    while (out.charAt(0) === sep) out = out.slice(1);
    out = out.slice(0, max);
    while (out.charAt(out.length - 1) === sep) out = out.slice(0, -1);
    return out;
  }

  function friendly(message) {
    return message.replace(/(programmes|channels)\[\d+\]\./g, '');
  }

  function field(label, control, hint) {
    return node('label', { 'class': 'rc-field' }, [node('span', { text: label }), control, hint ? node('small', { text: hint }) : null]);
  }

  function input(type, name, extra) {
    var props = { type: type, name: name, 'class': 'form-control form-control-sm' };
    Object.keys(extra || {}).forEach(function (k) { props[k] = extra[k]; });
    return node('input', props);
  }

  function choose(name, options) {
    var s = node('select', { name: name, 'class': 'form-control form-control-sm' });
    fillOptions(s, options);
    return s;
  }

  function fillOptions(sel, options) { // replace the options and keep the choice when it is still there
    var keep = sel.value;
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    options.forEach(function (o) { sel.appendChild(node('option', { value: String(o[0]), text: o[1] })); });
    if (options.some(function (o) { return String(o[0]) === keep; })) sel.value = keep;
  }

  function setupSay(text, isError) {
    setup.note.textContent = text;
    setup.note.className = 'rc-setup-note' + (isError ? ' rc-error' : '');
  }

  function savePlan(payload, done, afterSaving) {
    if (busy) return;
    busy = true;
    editing = null;
    setupSay('Saving…', false);
    send('PUT', payload, planApi).then(function () {
      return send('GET');
    }).then(function (data) {
      busy = false;
      plan = data;
      setup.dirty = {};
      setupSay(done, false);
      if (afterSaving) afterSaving();
      render();
    }).catch(function (error) {
      busy = false;
      setupSay(friendly(error.message), true);
    });
  }

  function nextNumber(channel) {
    var used = {};
    plan.channels.forEach(function (c) { c.programmes.forEach(function (p) { used[p.number] = true; }); });
    var n = Math.max(1, (channel ? channel.position : 1) * 100 + 1);
    while (used[n] && n < 9999) n += 1;
    return n;
  }

  function nextCell(channel) {
    var used = {};
    (channel ? channel.programmes : []).forEach(function (p) { used[p.cell] = true; });
    var c = 0;
    while (used[c]) c += 1;
    return c;
  }

  function channelBySlug(slug) {
    return plan.channels.filter(function (c) { return c.slug === slug; })[0] || null;
  }

  function looseById(id) {
    return plan.loose.filter(function (c) { return String(c.id) === String(id); })[0] || null;
  }

  function buildSetup() {
    var dirty = {};
    var s = { dirty: dirty };
    s.note = node('p', { 'class': 'rc-setup-note', role: 'status', 'aria-live': 'polite' });

    // 1. a channel
    s.cName = input('text', 'name', { maxlength: '80', placeholder: 'Mighty Street', required: 'required' });
    s.cSlug = input('text', 'slug', { maxlength: '40' });
    s.cPos = input('number', 'position', { min: '0', max: '99' });
    s.cKind = choose('kind', [['standard', 'An ordinary channel'], ['sponsored', 'A sponsored channel']]);
    s.cSponsor = input('text', 'sponsor_name', { maxlength: '80', placeholder: 'The sponsor\'s name' });
    s.cSponsorField = field('Sponsor', s.cSponsor);
    s.cSponsorField.hidden = true;
    s.cSynopsis = input('text', 'synopsis', { maxlength: '200', placeholder: 'One line about the channel (optional)' });
    var cSubmit = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: 'Add the channel' });
    s.channelForm = node('form', { 'class': 'rc-form', 'data-form': 'channel' }, [
      node('h3', { text: '1. A channel' }),
      node('p', { 'class': 'rc-hint', text: 'A channel is one row of the board: a theme with its programmes. It starts off air.' }),
      field('Name', s.cName), field('Short address', s.cSlug, 'Lower-case letters, digits and hyphens. Made from the name.'),
      field('Number on the dial', s.cPos), field('Kind', s.cKind), s.cSponsorField, field('One line about it', s.cSynopsis), cSubmit
    ]);
    s.cName.addEventListener('input', function () { if (!dirty.cSlug) s.cSlug.value = slugify(s.cName.value, '-', 40); });
    s.cSlug.addEventListener('input', function () { dirty.cSlug = true; });
    s.cPos.addEventListener('input', function () { dirty.cPos = true; });
    s.cKind.addEventListener('change', function () { s.cSponsorField.hidden = s.cKind.value !== 'sponsored'; });
    s.channelForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var name = s.cName.value.trim();
      if (!name) { setupSay('Give the channel a name.', true); return; }
      var item = { slug: s.cSlug.value.trim() || slugify(name, '-', 40), name: name, position: parseInt(s.cPos.value, 10), kind: s.cKind.value };
      if (isNaN(item.position)) { setupSay('Give the channel a number on the dial, from 0 to 99.', true); return; }
      if (item.kind === 'sponsored') item.sponsor_name = s.cSponsor.value.trim();
      if (s.cSynopsis.value.trim()) item.synopsis = s.cSynopsis.value.trim();
      savePlan({ channels: [item], programmes: [] }, 'Channel "' + name + '" added. It is off air. Next, put a challenge on it (step 2).',
        function () { s.cName.value = ''; s.cSlug.value = ''; s.cSynopsis.value = ''; });
    });

    // 2. a challenge on a channel
    s.pChallenge = choose('challenge', [['', '']]);
    s.pChannel = choose('channel', [['', '']]);
    s.pNumber = input('number', 'number', { min: '1', max: '9999' });
    s.pCell = input('number', 'cell', { min: '0', max: '999' });
    s.pSlug = input('text', 'slug', { maxlength: '48' });
    s.pDifficulty = choose('difficulty', DIFFICULTY_WORDS);
    s.pDifficulty.value = 'medium';
    s.pDelivery = choose('delivery', DELIVERY_WORDS);
    s.pHint = node('p', { 'class': 'rc-hint' });
    s.pSubmit = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: 'Put it on the channel' });
    s.programmeForm = node('form', { 'class': 'rc-form', 'data-form': 'programme' }, [
      node('h3', { text: '2. A challenge on a channel' }),
      node('p', { 'class': 'rc-hint', text: 'Make the challenge in CTFd first (Challenges, New). It starts off air here; the buttons below put it on.' }),
      s.pHint,
      field('Challenge', s.pChallenge), field('Channel', s.pChannel), field('Programme number', s.pNumber, 'Shown on the tile. Never used twice.'),
      field('Place on the picture', s.pCell, 'Counted from 0. Never used twice on a channel.'), field('Short name', s.pSlug, 'Lower-case letters, digits, hyphens and underscores.'),
      field('Difficulty', s.pDifficulty), field('How players get it', s.pDelivery), s.pSubmit
    ]);
    var defaults = function () {
      var challenge = looseById(s.pChallenge.value);
      var channel = channelBySlug(s.pChannel.value);
      if (!dirty.pSlug) s.pSlug.value = challenge ? slugify(challenge.name, '_', 48) : '';
      if (!dirty.pNumber) s.pNumber.value = channel ? nextNumber(channel) : '';
      if (!dirty.pCell) s.pCell.value = channel ? nextCell(channel) : '';
    };
    s.defaults = defaults;
    s.pChallenge.addEventListener('change', function () { dirty.pSlug = false; defaults(); });
    s.pChannel.addEventListener('change', function () { dirty.pNumber = false; dirty.pCell = false; defaults(); });
    ['pNumber', 'pCell', 'pSlug'].forEach(function (k) { s[k].addEventListener('input', function () { dirty[k] = true; }); });
    s.programmeForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var challenge = looseById(s.pChallenge.value);
      var channel = channelBySlug(s.pChannel.value);
      if (!challenge || !channel) { setupSay('Choose a challenge and a channel.', true); return; }
      var item = {
        challenge_id: challenge.id, channel: channel.slug, cell: parseInt(s.pCell.value, 10), number: parseInt(s.pNumber.value, 10), slug: s.pSlug.value.trim(),
        difficulty: s.pDifficulty.value, delivery: s.pDelivery.value
      };
      if (isNaN(item.cell) || isNaN(item.number)) { setupSay('Give the programme a number and a place on the picture.', true); return; }
      savePlan({ channels: [], programmes: [item] }, '"' + challenge.name + '" is on channel "' + channel.name + '" as programme ' + item.number +
        '. It is off air: press "Release now" on the channel and on the programme below when players should see it.');
    });

    // 3. a whole plan at once
    s.pJson = node('textarea', { name: 'plan', rows: '7', 'class': 'form-control form-control-sm rc-json', spellcheck: 'false', placeholder: '{"channels": [ ... ], "programmes": [ ... ]}' });
    var jSubmit = node('button', { type: 'submit', 'class': 'btn btn-sm btn-outline-primary', text: 'Load this plan' });
    s.jsonForm = node('form', { 'class': 'rc-form', 'data-form': 'json' }, [
      node('h3', { text: '3. Or a whole plan at once' }),
      node('p', { 'class': 'rc-hint', text: 'Paste the plan file the author kit made. The same rules apply; nothing goes on air by being loaded.' }),
      s.pJson, jSubmit
    ]);
    s.jsonForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var body;
      try { body = JSON.parse(s.pJson.value); } catch (error) { setupSay('That is not valid JSON: ' + error.message, true); return; }
      savePlan(body, 'The plan was loaded. Whatever is new is off air.', function () { s.pJson.value = ''; });
    });

    s.details = node('details', { 'class': 'rc-setup' }, [
      node('summary', { text: 'Set up the board: add a channel, put a challenge on it' }),
      s.note,
      node('div', { 'class': 'rc-forms' }, [s.channelForm, s.programmeForm, s.jsonForm])
    ]);
    return s;
  }

  function refreshSetup() {
    if (!setup) setup = buildSetup();
    var s = setup;
    var loose = plan.loose.map(function (c) { return [c.id, c.name + ' (' + c.category + ', ' + c.value + ' TRP)']; });
    var channels = plan.channels.map(function (c) { return [c.slug, 'CH ' + c.position + ' · ' + c.name]; });
    fillOptions(s.pChallenge, loose.length ? loose : [['', 'No challenge is waiting for a channel']]);
    fillOptions(s.pChannel, channels.length ? channels : [['', 'Add a channel first (step 1)']]);
    s.pSubmit.disabled = !loose.length || !channels.length;
    s.pHint.textContent = !loose.length ? 'Every challenge is already on a channel. Make a new one in CTFd first.' : '';
    if (!s.dirty.cPos) s.cPos.value = String(Math.min(99, Math.max.apply(null, [0].concat(plan.channels.map(function (c) { return c.position; }))) + 1));
    s.defaults();
    if (!s.opened && !plan.channels.length) { s.details.open = true; s.opened = true; } // the first time, with nothing on the board, the forms are open
  }

  function buildShell() {
    var reasonBox = node('input', { type: 'text', 'class': 'form-control form-control-sm', maxlength: '200', placeholder: 'Reason (kept in the audit trail)', 'aria-label': 'Reason for the change' });
    var refresh = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-primary', text: 'Refresh' });
    refresh.addEventListener('click', function () { editing = null; load(); });
    var panic = node('button', { type: 'button', 'class': 'btn btn-sm btn-danger', text: 'Withhold every channel' });
    panic.addEventListener('click', function () {
      if (!window.confirm('Take every channel off air for players?')) return;
      change(plan.channels.map(function (ch) { return { kind: 'channel', id: ch.id, mode: 'withhold' }; }));
    });
    var summary = node('p', { 'class': 'rc-summary' });
    var body = node('div', {});
    while (app.firstChild) app.removeChild(app.firstChild);
    app.appendChild(statusLine);
    app.appendChild(node('div', { 'class': 'rc-bar' }, [summary, reasonBox, refresh, panic]));
    if (!setup) setup = buildSetup();
    app.appendChild(setup.details);
    app.appendChild(body);
    shell = { summary: summary, reasonBox: reasonBox, body: body };
  }

  function render() {
    if (!plan) return;
    if (!shell) buildShell(); // built once: a poll only replaces what is below, so the reason box keeps its text and its focus
    refreshSetup();
    var counts = plan.counts;
    shell.summary.textContent = 'Now ' + istText(plan.now) + ' · broadcast ' + plan.phase.state + ' · ' + counts.on_air +
      (counts.on_air === 1 ? ' programme' : ' programmes') + ' on air, ' + counts.coming + ' coming up' +
      (plan.phase.state === 'before' ? ' · players see nothing on the board until the broadcast starts (' + istText(plan.start) + ')' : '');
    var loose = plan.loose.length ? node('section', { 'class': 'rc-loose' }, [
      node('h2', { 'class': 'h5', text: 'Not on any channel (so not on air)' }),
      node('ul', {}, plan.loose.map(function (c) { return node('li', { text: c.name }); }))
    ]) : null;
    var audit = node('section', { 'class': 'rc-audit' }, [
      node('h2', { 'class': 'h5', text: 'Latest changes' }),
      node('table', { 'class': 'table table-sm' }, [
        node('thead', {}, [node('tr', {}, ['When', 'Who', 'What', 'Where', 'Detail'].map(function (h) { return node('th', { text: h }); }))]),
        node('tbody', {}, plan.audit.map(function (a) {
          return node('tr', {}, [node('td', { text: istText(a.at) }), node('td', { text: a.actor }), node('td', { text: a.action }), node('td', { text: a.target }), node('td', { text: a.detail })]);
        }))
      ])
    ]);
    var children = (plan.channels.length ? plan.channels.map(channelBlock) : [node('p', { 'class': 'p-3', text: 'There are no channels yet. Add the first one under "Set up the board" above.' })]).concat([loose, audit]);
    while (shell.body.firstChild) shell.body.removeChild(shell.body.firstChild);
    children.forEach(function (child) { if (child) shell.body.appendChild(child); });
  }

  load();
  window.setInterval(load, REFRESH_MS);
}());
