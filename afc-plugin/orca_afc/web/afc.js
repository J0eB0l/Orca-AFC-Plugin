/*
 * AFC lanes page of the OrcaSlicer AFC plugin.
 *
 *   view "panel"  the AFC tab: units, lanes, dryers
 *   view "print"  lane mapping step before a print
 *
 * The plugin sets window.AFC_VIEW (and window.AFC_MODE = "upload" for an
 * upload without a print) before this script runs; a browser preview reads
 * ?view= and ?mode= instead. Talks to the plugin over window.orca.postMessage;
 * every printer change is built in Python from the fields sent here.
 */
(function () {
  'use strict';

  const params = new URLSearchParams(location.search);
  const VIEW = (window.AFC_VIEW || params.get('view')) === 'print' ? 'print' : 'panel';
  // Upload without a print: a print running now keeps its lanes, and the U1
  // switches go into the file instead of to the printer.
  const UPLOAD_ONLY = (window.AFC_MODE || params.get('mode')) === 'upload';
  // OrcaSlicer stamps its theme on the page; a browser preview follows the system.
  if (!document.documentElement.hasAttribute('data-orca-theme') && window.matchMedia &&
      matchMedia('(prefers-color-scheme: dark)').matches) {
    document.documentElement.classList.add('dark');
  }

  // ------------------------------------------------------------------ bridge
  let seq = 0;
  const pending = new Map();

  // A request is {afc_id, cmd, params}; the plugin answers {afc_id, ok, data | message}.
  function call(cmd, args) {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      try {
        window.orca.postMessage({ afc_id: id, cmd: cmd, params: args || {} });
      } catch (e) {
        pending.delete(id);
        reject(new Error('The AFC plugin is not available'));
        return;
      }
      // A lane load can keep Klipper busy for a minute before it answers.
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error('The AFC plugin did not answer'));
        }
      }, 90000);
    });
  }

  function onAnswer(data) {
    const id = data && data.afc_id;
    if (!id || !pending.has(id)) return;
    const p = pending.get(id);
    pending.delete(id);
    if (data.ok) p.resolve(data.data);
    else p.reject(new Error(data.message || 'failed'));
  }

  // ------------------------------------------------------------------ helpers
  const $app = document.getElementById('app');
  const $toast = document.getElementById('toast');

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  const ICONS = window.AFC_ICONS || {};
  function icon(name, cls) {
    const d = ICONS[name];
    return d ? '<svg class="ic' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>' : '';
  }

  let toastTimer = null;
  function toast(text, bad) {
    $toast.textContent = text;
    $toast.className = 'toast show' + (bad ? ' bad' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $toast.className = 'toast'; }, bad ? 5000 : 2600);
  }

  function hexOk(c) { return /^#[0-9A-Fa-f]{6}$/.test(c || ''); }

  function spoolStyle(lane) {
    const colors = (lane && lane.multi_colors && lane.multi_colors.length > 1) ? lane.multi_colors.filter(hexOk) : [];
    if (colors.length > 1) {
      const step = 360 / colors.length;
      return 'background: conic-gradient(' + colors.map((c, i) => c + ' ' + (i * step) + 'deg ' + ((i + 1) * step) + 'deg').join(', ') + ')';
    }
    const c = lane && hexOk(lane.color) ? lane.color : null;
    return c ? 'background:' + c : '';
  }

  function spool(lane, size) {
    const has = lane && lane.has_filament && (hexOk(lane.color) || (lane.multi_colors || []).length);
    const cls = size === true ? ' sm' : size ? ' ' + size : '';
    return '<span class="spool' + cls + (has ? '' : ' none') + '" style="' + (has ? spoolStyle(lane) : '') + '"></span>';
  }

  // Share of the spool left, 0-100, or -1 when AFC has no weight for it.
  function remaining(lane) {
    if (!lane || !lane.has_filament || !(lane.weight > 0)) return -1;
    const initial = lane.initial_weight > 0 ? lane.initial_weight : 1000;
    return Math.max(0, Math.min(100, (lane.weight / initial) * 100));
  }

  // A lane's spool with a ring for what is left on it.
  function reel(lane) {
    const pct = remaining(lane);
    const ring = pct < 0 ? '' : 'background: conic-gradient(var(' + (pct < 5 ? '--bad' : pct < 15 ? '--warn' : '--ok') + ') 0 ' +
      pct.toFixed(1) + '%, var(--surface-3) 0)';
    return '<span class="reel' + (pct < 0 ? ' none' : '') + '" style="' + ring + '">' + spool(lane, 'lg') + '</span>';
  }

  // The filament's color, to tint its lane card faintly.
  function tint(lane) {
    const c = lane && lane.has_filament ? rgb(lane.color) : null;
    return c ? '--fil: ' + c.join(',') : '';
  }

  function swatch(color, small) {
    return '<span class="spool' + (small ? ' sm' : '') + (hexOk(color) ? '' : ' none') + '" style="' + (hexOk(color) ? 'background:' + color : '') + '"></span>';
  }

  // Toolhead index of an AFC extruder name, as the slicer reads it: extruder -> 0, extruder2 -> 2, e1 -> 1.
  function extruderIndex(name) {
    const m = /^(?:extruder(\d*)|e(\d+))$/.exec(name || '');
    return m ? +(m[1] || m[2] || 0) : -1;
  }

  // A toolhead goes by its AFC extruder name (e0, extruder1), as in AFC's own config.
  function headName(idx) {
    const afc = state && state.afc;
    const lane = afc && afc.lanes.find((l) => l.extruder_index === idx && l.extruder);
    if (lane) return lane.extruder;
    const name = afc && (afc.extruders || []).find((e) => extruderIndex(e) === idx);
    return name || (idx >= 0 ? 'extruder' + (idx || '') : 'toolhead');
  }

  // The unit's icon, picked the way Fluidd picks it: by AFC unit type, and for a
  // Bambu unit by the model its dryer reports. Other units show the AFC logo.
  function unitIconName(unit) {
    const type = String(unit.type || '').toLowerCase().replace(/[_\s-]/g, '');
    const model = String((unit.dryer && unit.dryer.model) || '').toLowerCase();
    // OpenAMS before the Bambu rule: its type contains "ams" too.
    if (/openams|oams/.test(type)) return 'openams';
    if (/bambu|^ams/.test(type)) {
      if (model === 'ht' || (!model && /(?:^|_)ht(?:_|$)/i.test(unit.name || ''))) return 'amsht';
      return model === 'ams2' ? 'ams2' : 'ams';
    }
    if (/^ace/.test(type)) return /2/.test(type + model) ? 'ace2' : 'ace';
    if (/boxturtle/.test(type)) return 'boxturtle';
    if (/nightowl/.test(type)) return 'nightowl';
    if (/htlf/.test(type)) return 'htlf';
    if (/quattro/.test(type)) return 'quattrobox';
    return 'afc';
  }
  function unitIcon(unit) { return '<span class="unit-icon" title="' + esc(prettyType(unit.type)) + '">' + icon(unitIconName(unit)) + '</span>'; }
  function prettyType(type) { return (type || 'Unit').replace(/_/g, ' '); }

  function materialKey(m) {
    return String(m || '').toUpperCase().split(/[ \-_]/)[0].replace(/\+$/, '');
  }

  function rgb(c) {
    if (!hexOk(c)) return null;
    return [parseInt(c.substr(1, 2), 16), parseInt(c.substr(3, 2), 16), parseInt(c.substr(5, 2), 16)];
  }
  function colorDistance(a, b) {
    const x = rgb(a), y = rgb(b);
    if (!x || !y) return 0;
    return Math.sqrt((x[0] - y[0]) ** 2 + (x[1] - y[1]) ** 2 + (x[2] - y[2]) ** 2);
  }

  function laneSubtitle(lane) {
    if (!lane.has_filament) return 'Empty';
    const parts = [lane.material || 'Unknown material'];
    if (lane.vendor) parts.push(lane.vendor);
    return parts.join(' · ');
  }

  function grams(v) { return Math.round(v) + ' g'; }

  // Flow K (pressure advance) saved on the lane's Spoolman spool by AFC autocal.
  function flowK(lane) { return lane && typeof lane.flow_k === 'number' ? 'K ' + +lane.flow_k.toFixed(4) : ''; }

  // ------------------------------------------------------------------ state
  let state = null;          // last answer from get_state
  let selected = null;       // lane name open in the editor (panel)
  let editorDirty = false;
  let busy = false;
  let mapping = null;        // print view: filament id -> lane name
  let openMenu = null;       // print view: filament id with its picker open
  let u1Print = null;        // print view: the U1's print switches as picked here
  let u1SetFailed = false;   // print view: the printer refused them once; the next try prints without
  let dryUnit = null;        // panel: unit name with the dry popup open
  let dryForm = null;        // panel: what the dry popup holds {material, temp, hours, rotate}
  let dryStarting = null;    // panel: {unit, temp, until} from a start until the unit reports drying

  function lanes() { return (state && state.afc && state.afc.lanes) || []; }
  // Units with lanes; AFC also lists a toolchanger entry and unclaimed BridgeBox spares.
  function laneUnits() { return ((state && state.afc && state.afc.units) || []).filter((u) => (u.lanes || []).length); }
  function unitByName(name) { return ((state && state.afc && state.afc.units) || []).find((u) => u.name === name); }
  function laneByName(name) { return lanes().find((l) => l.name === name); }

  let stateJson = '';         // `state` as last drawn, to skip redrawing an unchanged poll

  async function load(refresh) {
    try {
      const data = await call('get_state', { refresh: refresh !== false, project: VIEW === 'print' });
      const json = JSON.stringify(data);
      const unchanged = state !== null && json === stateJson;
      state = data;
      stateJson = json;
      if (data && data.error) toast(data.error, true);
      if (VIEW === 'print' && mapping === null) resetMapping();
      if (VIEW === 'print' && u1Print === null && data && data.afc && data.afc.u1_print) u1Print = Object.assign({}, data.afc.u1_print);
      if (!unchanged) render();
    } catch (e) {
      if (!state) renderError(e.message);
      else toast(e.message, true);
    }
  }

  // A redraw replaces the scrolling boxes, which would send them back to the top
  // every time the panel polls, so each one keeps its place.
  function render() {
    const scrolled = [];
    for (const el of $app.querySelectorAll('[id]'))
      if (el.scrollTop || el.scrollLeft) scrolled.push([el.id, el.scrollTop, el.scrollLeft]);
    if (VIEW === 'print') renderPrint();
    else renderPanel();
    for (const [id, top, left] of scrolled) {
      const el = document.getElementById(id);
      if (el) { el.scrollTop = top; el.scrollLeft = left; }
    }
  }

  function renderError(message) {
    $app.innerHTML = '<div class="empty-state"><div class="logo">' + icon('afc') + '</div><div class="big">Could not read AFC</div>' + esc(message) +
      '<div style="margin-top:14px"><button class="btn" id="retry">Try again</button></div></div>';
    document.getElementById('retry').onclick = () => load(true);
  }

  // ================================================================== panel
  function renderPanel() {
    const afc = state && state.afc;
    if (!state || !state.connected) {
      $app.innerHTML = bar('', '') + '<div class="empty-state"><div class="logo">' + icon('afc') + '</div><div class="big">No printer address</div>' +
        esc((state && state.hint) || 'Set the printer\'s address in its connection settings to see its AFC units.') + '</div>';
      bindBar();
      return;
    }
    if (!afc || !afc.present || !afc.lanes.length) {
      $app.innerHTML = bar('', '') + '<div class="empty-state"><div class="logo">' + icon('afc') + '</div><div class="big">No AFC on this printer</div>When AFC is installed, its units and lanes show up here.</div>';
      bindBar();
      return;
    }

    const loadedCount = afc.lanes.filter((l) => l.has_filament).length;
    const units = laneUnits();
    const stats = '<div class="stats"><span class="stat"><b>' + units.length + '</b>unit' + (units.length === 1 ? '' : 's') + '</span>' +
      '<span class="stat"><b>' + loadedCount + '</b>of ' + afc.lanes.length + ' lanes loaded</span></div>';

    let html = bar('AFC ' + esc(afc.version || '') + (afc.spoolman ? ' · Spoolman' : ''), stats);
    html += '<div class="main"><div class="scroll" id="scroll">' + headsStrip(afc);
    for (const unit of units) html += unitCard(unit);
    html += '</div>';
    if (selected && laneByName(selected)) html += editor(laneByName(selected));
    html += '</div>';
    const dryOpen = dryUnit && unitByName(dryUnit) && unitByName(dryUnit).dryer;
    if (dryOpen) html += dryPopup(unitByName(dryUnit));
    $app.innerHTML = html;
    bindBar();
    for (const el of document.querySelectorAll('[data-dry]')) {
      el.onclick = (e) => { e.stopPropagation(); openDry(el.dataset.dry); };
    }
    if (dryOpen) bindDry(unitByName(dryUnit));
    for (const el of document.querySelectorAll('.lane[data-lane]')) {
      el.onclick = () => { selected = el.dataset.lane === selected ? null : el.dataset.lane; editorDirty = false; dryUnit = null; render(); };
    }
    if (selected) bindEditor(laneByName(selected));
  }

  function bar(sub, extra) {
    return '<div class="bar"><span class="logo">' + icon('afc') + '</span><div><h1>AFC lanes</h1>' + (sub ? '<div class="sub">' + sub + '</div>' : '') + '</div>' +
      '<div class="grow"></div>' + extra + refreshButton() + '</div>';
  }

  function refreshButton() {
    return '<button class="btn icon ghost" id="refresh" title="Read AFC again">' + icon('refresh') + '</button>';
  }

  function bindBar() {
    const r = document.getElementById('refresh');
    if (r) r.onclick = () => load(true);
  }

  function headsStrip(afc) {
    const heads = new Map();
    for (const name of afc.extruders || []) {
      const idx = extruderIndex(name);
      heads.set(idx >= 0 ? idx : heads.size, null);
    }
    for (const l of afc.lanes) {
      if (l.extruder_index >= 0 && !heads.has(l.extruder_index)) heads.set(l.extruder_index, null);
      if (l.tool_loaded && l.extruder_index >= 0) heads.set(l.extruder_index, l);
    }
    if (heads.size < 2 && !afc.lanes.some((l) => l.tool_loaded)) return '';
    let html = '<div class="section-label">Toolheads</div><div class="heads">';
    for (const [idx, lane] of [...heads.entries()].sort((a, b) => a[0] - b[0])) {
      const active = lane && lane.name === afc.current_lane;
      html += '<div class="head' + (lane ? ' loaded' : '') + (active ? ' active' : '') + '"><span class="head-ic">' + icon('nozzle') + (lane ? spool(lane) : '') + '</span>' +
        '<div style="min-width:0"><div class="label">' + esc(headName(idx)) + (active ? '<span class="pill accent">Active</span>' : '') + '</div>' +
        '<div class="what">' + (lane ? esc(lane.name) + ' · ' + esc(lane.material || '') : '<span class="hint">Empty</span>') + '</div></div></div>';
    }
    return html + '</div>';
  }

  function unitCard(unit) {
    const here = unit.lanes.map(laneByName).filter(Boolean);
    const loaded = here.filter((l) => l.has_filament).length;
    const model = unit.dryer && unit.dryer.model && !/^ace/i.test(unit.dryer.model) ? ' · ' + esc(bambuModel(unit.dryer.model)) : '';
    let html = '<div class="unit"><div class="unit-head">' + unitIcon(unit) +
      '<div><div class="name">' + esc(unit.name.replace(/_/g, ' ')) + '</div><div class="type">' + esc(prettyType(unit.type)) + model +
      ' · ' + loaded + ' of ' + here.length + ' loaded</div></div>' +
      '<div class="grow"></div>' + dryChip(unit) + '</div><div class="lanes">';
    for (const l of here) html += laneTile(l);
    return html + '</div></div>';
  }

  function laneTile(l) {
    const maps = (l.maps || []).length ? l.maps.map((m) => '<span class="tchip">' + esc(m) + '</span>').join('') : '<span class="tchip none">no T</span>';
    let status = '';
    if (l.tool_loaded) status = '<span class="pill ok"><span class="dot"></span>In ' + esc(headName(l.extruder_index)) + '</span>';
    if (l.spool_id) status += '<span class="pill accent" title="Spoolman spool">#' + esc(l.spool_id) + '</span>';
    if (flowK(l)) status += '<span class="pill" title="Flow K (pressure advance) saved on the spool">' + esc(flowK(l)) + '</span>';
    const pct = remaining(l);
    const where = l.extruder_index >= 0 ? '<span class="where" title="Feeds ' + esc(headName(l.extruder_index)) + '">' + icon('nozzle') + esc(headName(l.extruder_index)) + '</span>' : '';
    return '<div class="lane' + (l.has_filament ? '' : ' empty') + (l.tool_loaded ? ' in-tool' : '') + (l.name === selected ? ' selected' : '') +
      '" data-lane="' + esc(l.name) + '" style="' + tint(l) + '">' +
      '<div class="lane-top">' + reel(l) + '<div class="lane-id"><div class="lane-name"><span>' + esc(l.name) + '</span>' + maps + '</div>' +
      '<div class="lane-sub" title="' + esc(l.filament_name || '') + '">' + esc(laneSubtitle(l)) + '</div></div></div>' +
      '<div class="lane-foot"><span>' + (pct >= 0 ? '<b>' + grams(l.weight) + '</b> · ' + Math.round(pct) + '%' : l.has_filament ? 'No weight' : '') + '</span>' + where + '</div>' +
      (status ? '<div class="lane-meta">' + status + '</div>' : '') +
      '</div>';
  }

  // ------------------------------------------------------------------ dryer
  // Drying spools inside a closed unit, after Bambu Lab's drying table for the AMS HT (PLA and PETG match its
  // AMS 2 Pro column too). A unit with a lower ceiling (ACE 55 °C, AMS 2 Pro 65 °C) dries at its ceiling instead.
  const DRY_PRESETS = [
    { m: 'PLA', t: 45, h: 12 }, { m: 'PETG', t: 65, h: 12 }, { m: 'TPU', t: 75, h: 18 }, { m: 'ABS', t: 80, h: 8 },
    { m: 'ASA', t: 80, h: 8 }, { m: 'PC', t: 80, h: 8 }, { m: 'PA', t: 85, h: 12 }, { m: 'PVA', t: 85, h: 18 },
  ];
  const DRY_ALIASES = { PA6: 'PA', PA11: 'PA', PA12: 'PA', PAHT: 'PA', NYLON: 'PA', PCTG: 'PETG' };

  function bambuModel(m) { return ({ ams2: 'AMS 2 Pro', ht: 'AMS HT', ams1: 'AMS', boxed: 'AMS', lite: 'AMS lite' })[m] || m; }

  function duration(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h ? h + ' h' + (m ? ' ' + m + ' min' : '') : m + ' min';
  }

  function climate(d) {
    const parts = [];
    if (d.temperature >= 0) parts.push(Math.round(d.temperature) + ' °C');
    if (d.humidity >= 0) parts.push(Math.round(d.humidity) + '% RH');
    return parts.join(' · ');
  }

  // The unit's temperature and humidity with their icons, or '' with no reading.
  function climateIcons(d, cls) {
    let html = '';
    if (d.temperature >= 0) html += '<span title="Temperature in the unit">' + icon('temp') + Math.round(d.temperature) + ' °C</span>';
    if (d.humidity >= 0) html += '<span title="Relative humidity in the unit">' + icon('humidity') + Math.round(d.humidity) + '%</span>';
    return html ? '<span class="' + cls + '">' + html + '</span>' : '';
  }

  // An ACE reports a start on its next status poll, a few seconds after the start
  // command, so for a while after one the unit shows as starting.
  function dryStartingFor(unit) {
    if (!dryStarting || dryStarting.unit !== unit.name) return false;
    if (unit.dryer && unit.dryer.drying) {
      dryStarting = null;
      return false;
    }
    if (Date.now() > dryStarting.until) {
      dryStarting = null;
      toast(unit.name.replace(/_/g, ' ') + ' has not reported drying. Check the unit and try again.', true);
      return false;
    }
    return true;
  }

  function dryChip(unit) {
    const d = unit.dryer;
    if (!d) return '';
    if (!d.drying && dryStartingFor(unit)) {
      return '<button class="dry-chip on" data-dry="' + esc(unit.name) + '" title="Starting: open the dryer">' +
        icon('dry') + 'Starting at ' + Math.round(dryStarting.temp) + ' °C</button>';
    }
    if (d.drying) {
      return climateIcons(d, 'climate') + '<button class="dry-chip on" data-dry="' + esc(unit.name) + '" title="Drying: open the dryer">' +
        icon('dry') + Math.round(d.target) + ' °C · ' + duration(d.remaining_s) + ' left</button>';
    }
    return climateIcons(d, 'climate') +
      '<button class="btn sm" data-dry="' + esc(unit.name) + '"' + (d.online ? '' : ' disabled title="The unit is offline"') + '>' + icon('dry') + 'Dry</button>';
  }

  function dryKey(material) {
    const key = materialKey(material);
    return DRY_ALIASES[key] || key;
  }

  function presetFor(material, max) {
    const key = dryKey(material);
    const p = DRY_PRESETS.find((x) => x.m === key) || DRY_PRESETS[0];
    return { material: p.m, temp: Math.min(p.t, max), hours: p.h, ideal: p.t };
  }

  function openDry(name) {
    const unit = unitByName(name);
    if (!unit || !unit.dryer) return;
    const counts = {};
    for (const ln of unit.lanes) {
      const l = laneByName(ln);
      const k = l && l.has_filament ? dryKey(l.material) : '';
      if (k) counts[k] = (counts[k] || 0) + 1;
    }
    const common = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || 'PLA';
    dryForm = Object.assign(presetFor(common, unit.dryer.max_temp || 85), { rotate: false });
    dryUnit = name;
    selected = null;
    editorDirty = false;
    render();
  }

  function dryPopup(unit) {
    const d = unit.dryer;
    const max = Math.round(d.max_temp || 85);
    const f = dryForm || presetFor('PLA', max);
    const model = d.model ? (/^ace/i.test(d.model) ? d.model : bambuModel(d.model)) : prettyType(unit.type);
    const lanesHere = unit.lanes.map(laneByName).filter(Boolean);
    const inUse = lanesHere.filter((l) => l.tool_loaded).length;
    const mats = new Set(lanesHere.filter((l) => l.has_filament).map((l) => dryKey(l.material)).filter(Boolean));
    let body;
    if (d.drying) {
      const total = d.duration_s > 0 ? d.duration_s : 0;
      const pct = total ? Math.max(0, Math.min(100, 100 * (1 - d.remaining_s / total))) : 0;
      body = '<div class="dry-live"><div class="dry-big">' + icon('dry') + Math.round(d.target) + ' °C</div>' +
        '<div><div>' + duration(d.remaining_s) + ' left' + (total ? ' of ' + duration(total) : '') + '</div>' +
        '<div class="hint">' + esc(climate(d) || 'No reading yet') + (d.rotate ? ' · spools turning' : '') + '</div></div></div>' +
        (total ? '<div class="meter"><i style="width:' + pct.toFixed(0) + '%"></i></div>' : '') +
        (d.note ? '<div class="pill warn" style="margin-top:10px">' + esc(d.note) + '</div>' : '') +
        '<div class="actions"><div class="grow"></div><button class="btn ghost" id="dry-close">Close</button>' +
        '<button class="btn danger" id="dry-stop">Stop drying</button></div>';
    } else if (dryStartingFor(unit)) {
      body = '<div class="dry-live"><div class="dry-big">' + icon('dry') + Math.round(dryStarting.temp) + ' °C</div>' +
        '<div><div>Starting the dryer…</div><div class="hint">' + esc(climate(d) || 'Waiting for the unit') + '</div></div></div>' +
        '<div class="actions"><div class="grow"></div><button class="btn ghost" id="dry-close">Close</button></div>';
    } else {
      body = (climateIcons(d, 'dry-climate') || '<div class="dry-climate">No temperature or humidity reading</div>') +
        '<div class="presets">' + DRY_PRESETS.map((p) => '<button class="preset' + (f.material === p.m ? ' cur' : '') + (mats.has(p.m) ? ' here' : '') +
          '" data-preset="' + p.m + '"' + (p.t > max ? ' title="' + p.m + ' usually dries at ' + p.t + ' °C"' : '') + '><b>' + p.m + '</b>' +
          '<span' + (p.t > max ? ' class="capped"' : '') + '>' + Math.min(p.t, max) + ' °C · ' + p.h + ' h</span></button>').join('') + '</div>' +
        (f.material && f.ideal > max ? '<div class="hint dry-cap">The ' + esc(model) + ' dries at ' + max + ' °C at most. ' + esc(f.material) +
          ' usually dries at ' + f.ideal + ' °C, so it may not dry fully.</div>' : '') +
        '<div class="form">' +
        '<div class="field"><label>Temperature (°C, up to ' + max + ')</label><input id="dry-temp" type="number" min="35" max="' + max + '" step="1" value="' + esc(f.temp) + '"></div>' +
        '<div class="field"><label>Time (hours)</label><input id="dry-hours" type="number" min="0.5" max="48" step="0.5" value="' + esc(f.hours) + '"></div>' +
        '<label class="field wide check"><input type="checkbox" id="dry-rotate"' + (f.rotate ? ' checked' : '') + '> Turn the spools while drying' +
        '<span class="hint">AFC skips turning while a lane is loaded to the hub or a toolhead' + (inUse ? ' (' + inUse + ' in a toolhead now)' : '') + '.</span></label>' +
        '</div>' +
        (d.note ? '<div class="pill warn" style="margin-top:10px">' + esc(d.note) + '</div>' : '') +
        '<div class="hint dry-foot">Presets are for drying spools inside the unit; check the spool\'s label.</div>' +
        '<div class="actions"><div class="grow"></div>' +
        '<button class="btn ghost" id="dry-close">Cancel</button><button class="btn primary" id="dry-start">Start drying</button></div>';
    }
    return '<div class="modal-back" id="dry-back"><div class="modal" id="dry-modal" role="dialog">' +
      '<h2>' + unitIcon(unit) + 'Dry ' + esc(unit.name.replace(/_/g, ' ')) +
      '<span class="hint">' + esc(model) + '</span></h2>' + body + '</div></div>';
  }

  function bindDry(unit) {
    const $ = (id) => document.getElementById(id);
    const close = () => { dryUnit = null; dryForm = null; render(); };
    $('dry-close').onclick = close;
    $('dry-back').onclick = (e) => { if (e.target.id === 'dry-back') close(); };
    const max = Math.round(unit.dryer.max_temp || 85);
    if (unit.dryer.drying) {
      $('dry-stop').onclick = async () => {
        await run(() => call('dry', { unit: unit.name, action: 'stop' }).then((data) => {
          // The unit may report the stop a few seconds later; the card catches up on the next read.
          if (data && !data.error) {
            dryUnit = null;
            dryForm = null;
            setTimeout(() => load(true), 1500);
          }
          return data;
        }), 'Dryer stopped');
      };
      return;
    }
    if (!$('dry-start')) return;  // starting: only Close
    for (const el of document.querySelectorAll('[data-preset]')) {
      el.onclick = () => { dryForm = Object.assign(presetFor(el.dataset.preset, max), { rotate: $('dry-rotate').checked }); render(); };
    }
    const keep = () => {
      dryForm = { material: '', temp: parseFloat($('dry-temp').value), hours: parseFloat($('dry-hours').value), rotate: $('dry-rotate').checked };
    };
    for (const id of ['dry-temp', 'dry-hours', 'dry-rotate']) $(id).addEventListener('change', keep);
    $('dry-start').onclick = async () => {
      keep();
      const temp = Math.round(dryForm.temp), minutes = Math.round(dryForm.hours * 60);
      if (!(temp >= 35 && temp <= max)) return toast('Temperature must be 35-' + max + ' °C', true);
      if (!(minutes >= 1 && minutes <= 2880)) return toast('Time must be up to 48 hours', true);
      await run(() => call('dry', { unit: unit.name, action: 'start', temp: temp, minutes: minutes, rotate: dryForm.rotate }).then((data) => {
        if (data && !data.error) {
          dryStarting = { unit: unit.name, temp: temp, until: Date.now() + 30000 };
          setTimeout(() => load(true), 1500);
        }
        return data;
      }), 'Drying ' + unit.name.replace(/_/g, ' ') + ' at ' + temp + ' °C');
    };
  }

  const MATERIALS = ['PLA', 'PLA+', 'PLA-CF', 'PETG', 'PETG-CF', 'ABS', 'ASA', 'TPU', 'PA', 'PA-CF', 'PC', 'PVA', 'HIPS', 'PET', 'PPS'];

  function editor(l) {
    const maxTool = Math.max(state.afc.max_tool, (l.maps || []).reduce((a, m) => Math.max(a, +m.slice(1) || 0), 0));
    let tOptions = '';
    for (let t = 0; t <= maxTool; t++) {
      const owner = state.afc.lanes.find((x) => (x.maps || []).includes('T' + t));
      tOptions += '<option value="T' + t + '"' + ((l.maps || [])[0] === 'T' + t ? ' selected' : '') + '>T' + t + (owner && owner.name !== l.name ? ' (now ' + esc(owner.name) + ')' : '') + '</option>';
    }
    const color = hexOk(l.color) ? l.color : '#FFFFFF';
    return '<div class="drawer" id="drawer"><div class="sheet-head">' + spool(l) +
      '<div class="grow"><h2>' + esc(l.name) + '</h2><span class="hint">' + esc(l.unit.replace(/_/g, ' ')) + (l.extruder_index >= 0 ? ' · feeds ' + esc(headName(l.extruder_index)) : '') + '</span></div>' +
      '<button class="btn icon ghost" id="close" title="Close">' + icon('close') + '</button></div>' +
      '<div class="sheet-body"><div class="form">' +
      '<div class="field"><label>Color</label><div class="color-row"><input type="color" id="f-colorpick" value="' + esc(color) + '"><input id="f-color" value="' + esc(color) + '" maxlength="7"></div></div>' +
      '<div class="field"><label>Material</label><input id="f-material" list="materials" value="' + esc(l.material || '') + '"><datalist id="materials">' + MATERIALS.map((m) => '<option value="' + m + '">').join('') + '</datalist></div>' +
      '<div class="field"><label>Remaining (g)</label><input id="f-weight" type="number" min="0" step="1" value="' + esc(Math.round(l.weight || 0)) + '"></div>' +
      '<div class="field"><label>Tool (T number)</label><select id="f-map">' + (l.maps && l.maps.length ? '' : '<option value="">none</option>') + tOptions + '</select></div>' +
      '<div class="field"><label>Nozzle °C</label><input id="f-nozzle" type="number" min="0" max="500" value="' + esc(l.nozzle_temp || '') + '"></div>' +
      '<div class="field"><label>Bed °C</label><input id="f-bed" type="number" min="0" max="200" value="' + esc(l.bed_temp || '') + '"></div>' +
      '<div class="field wide"><label>Spoolman spool ID' + (state.afc.spoolman ? '' : ' (Spoolman is not set up in AFC)') + '</label>' +
      '<div class="color-row"><input id="f-spool" type="number" min="0" step="1" value="' + esc(l.spool_id || '') + '" placeholder="none">' +
      (state.afc.spoolman_url ? '<button class="btn" id="open-spool"' + (l.spool_id ? '' : ' disabled') + ' title="Open this spool in Spoolman">' + icon('open') + 'Open in Spoolman</button>' : '') + '</div>' +
      '<span class="hint">Setting a spool ID makes AFC fill color, material and weight from Spoolman.' +
      (flowK(l) ? ' The spool\'s saved flow K (pressure advance) is ' + esc(flowK(l).slice(2)) + '.' : '') + '</span></div>' +
      '</div>' +
      '<div class="lane-actions">' +
      '<button class="btn" id="act-load"' + (l.has_filament && !l.tool_loaded ? '' : ' disabled') + ' title="CHANGE_TOOL: swap this lane into its toolhead">' + icon('load') + 'Load</button>' +
      '<button class="btn" id="act-unload"' + (l.tool_loaded ? '' : ' disabled') + ' title="TOOL_UNLOAD: pull it out of the toolhead">' + icon('unload') + 'Unload</button>' +
      '<button class="btn danger" id="act-eject"' + (l.has_filament && !l.tool_loaded ? '' : ' disabled') + ' title="LANE_UNLOAD: retract it out of the unit">' + icon('eject') + 'Eject</button></div></div>' +
      '<div class="sheet-foot"><button class="btn primary" id="save" disabled>Save</button></div></div>';
  }

  function bindEditor(l) {
    const $ = (id) => document.getElementById(id);
    const save = $('save');
    const dirty = () => { editorDirty = true; save.disabled = false; };
    for (const id of ['f-color', 'f-material', 'f-weight', 'f-map', 'f-nozzle', 'f-bed', 'f-spool']) {
      $(id).addEventListener('input', dirty);
      $(id).addEventListener('change', dirty);
    }
    $('f-colorpick').addEventListener('input', () => { $('f-color').value = $('f-colorpick').value.toUpperCase(); dirty(); });
    $('f-color').addEventListener('input', () => { if (hexOk($('f-color').value)) $('f-colorpick').value = $('f-color').value; });
    $('close').onclick = () => { selected = null; editorDirty = false; render(); };
    const open = $('open-spool');
    if (open) open.onclick = () => call('open_spoolman', { spool_id: l.spool_id }).catch((e) => toast(e.message, true));

    save.onclick = async () => {
      const changes = {};
      const spoolVal = $('f-spool').value.trim();
      const spoolId = spoolVal === '' ? 0 : parseInt(spoolVal, 10);
      if (spoolId !== (l.spool_id || 0)) {
        // Spoolman owns the rest once a spool is linked; send the ID alone.
        changes.spool_id = spoolId;
      } else {
        const color = $('f-color').value.trim().toUpperCase();
        if (!hexOk(color)) return toast('Color must look like #RRGGBB', true);
        if (color !== (l.color || '').toUpperCase()) changes.color = color;
        const material = $('f-material').value.trim();
        if (material && material !== l.material) changes.material = material;
        const weight = parseFloat($('f-weight').value);
        if (!isNaN(weight) && Math.round(weight) !== Math.round(l.weight || 0)) changes.weight = weight;
        const map = $('f-map').value;
        if (map && map !== (l.maps || [])[0]) changes.map = map;
        const nozzle = parseInt($('f-nozzle').value, 10);
        if (!isNaN(nozzle) && nozzle !== l.nozzle_temp) changes.nozzle_temp = nozzle;
        const bed = parseInt($('f-bed').value, 10);
        if (!isNaN(bed) && bed !== l.bed_temp) changes.bed_temp = bed;
      }
      if (!Object.keys(changes).length) { editorDirty = false; save.disabled = true; return; }
      await run(() => call('edit_lane', { lane: l.name, changes: changes }), changes.spool_id !== undefined ? 'Spool linked; AFC is pulling it from Spoolman' : 'Saved to AFC');
      editorDirty = false;
    };

    const action = (name) => async () => {
      await run(() => call('lane_action', { lane: l.name, action: name }), 'Sent to AFC');
    };
    $('act-load').onclick = action('load');
    $('act-unload').onclick = action('unload');
    $('act-eject').onclick = action('eject');
  }

  async function run(fn, okText) {
    if (busy) return;
    busy = true;
    $app.classList.add('busy');
    try {
      const data = await fn();
      if (data && data.afc) state = data.project ? data : Object.assign({}, state, { afc: data.afc, connected: data.connected });
      if (data && data.error) toast(data.error, true);
      else if (okText) toast(okText);
    } catch (e) {
      toast(e.message, true);
    } finally {
      busy = false;
      $app.classList.remove('busy');
      render();
    }
  }

  // ================================================================== print
  function filaments() { return (state && state.project && state.project.filaments) || []; }

  function resetMapping() {
    mapping = {};
    const sug = (state && state.project && state.project.suggestion) || [];
    filaments().forEach((f, i) => { if (f.used) mapping[f.id] = sug[i] || ''; });
  }

  function rowWarnings(f) {
    const out = [];
    const laneName = mapping[f.id];
    if (!laneName) return [['bad', 'Pick a lane']];
    const l = laneByName(laneName);
    if (!l) return [['bad', 'Lane is gone']];
    if (!l.has_filament) out.push(['bad', laneName + ' is empty']);
    const dup = filaments().filter((o) => o.used && o.id !== f.id && mapping[o.id] === laneName);
    if (dup.length && !state.afc.multiple_mapping) out.push(['bad', 'Also picked for ' + dup.map((o) => 'T' + o.id).join(', ') + '; AFC multiple tool mapping is off']);
    if (l.material && f.type && materialKey(l.material) !== materialKey(f.type)) out.push(['warn', 'Lane has ' + l.material + ', the plate uses ' + f.type]);
    if (f.grams > 0 && l.weight > 0 && l.weight < f.grams) out.push(['warn', 'Needs ' + grams(f.grams) + ', about ' + grams(l.weight) + ' left']);
    if (l.has_filament && colorDistance(l.color, f.color) > 120) out.push(['', 'Different color']);
    // The slot was sliced with the pressure advance of the spool it was synced from.
    if (l.has_filament && f.spool_pa >= 0 && typeof l.flow_k === 'number' && Math.abs(f.spool_pa - l.flow_k) > 1e-6)
      out.push(['', 'Sliced with K ' + +f.spool_pa.toFixed(4) + ', this spool has ' + flowK(l)]);
    return out;
  }

  function renderPrint() {
    const afc = state && state.afc;
    if (!afc || !afc.present || !afc.lanes.length) {
      $app.innerHTML = '<div class="empty-state"><div class="big">AFC is not answering</div>Continue to print with the lanes AFC has mapped now.</div>' +
        '<div class="footer"><div class="grow"></div><button class="btn" id="cancel">Cancel</button><button class="btn primary" id="skip">Continue</button></div>';
      document.getElementById('cancel').onclick = () => finish('cancel');
      document.getElementById('skip').onclick = () => finish('continue');
      return;
    }
    const fils = filaments();
    const used = fils.filter((f) => f.used);
    let blocking = false;
    let rows = '';
    for (const f of [...used, ...fils.filter((x) => !x.used)]) {
      const warns = f.used ? rowWarnings(f) : [];
      if (warns.some((w) => w[0] === 'bad')) blocking = true;
      const l = laneByName(mapping[f.id]);
      rows += '<div class="map-row' + (f.used ? '' : ' unused') + '">' +
        '<div class="fil">' + swatch(f.color) + '<div style="min-width:0"><div class="t">T' + f.id + ' · ' + esc(f.type || '?') + '</div>' +
        '<div class="s" title="' + esc(f.preset) + '">' + (f.used ? (f.grams > 0 ? grams(f.grams) + ' · ' : '') + esc(f.preset || '') : 'Not used on this plate') + '</div></div></div>' +
        '<div class="arrow">' + icon('arrow') + '</div>' +
        (f.used ? picker(f, l) : '<div class="hint">—</div>') +
        '<div class="warns">' + warns.map((w) => '<span class="pill ' + w[0] + '">' + esc(w[1]) + '</span>').join('') + '</div></div>';
    }

    $app.innerHTML =
      '<div class="bar"><span class="logo">' + icon('afc') + '</span><div><h1>Match filaments to AFC lanes</h1><div class="sub">' + used.length + ' filament' + (used.length === 1 ? '' : 's') + ' on this plate · ' +
      afc.lanes.filter((x) => x.has_filament).length + ' loaded lanes in ' + laneUnits().length + ' unit' + (laneUnits().length === 1 ? '' : 's') + '</div></div>' +
      '<div class="grow"></div><button class="btn" id="auto" title="Match by material, then color">Auto match</button>' +
      refreshButton() + '</div>' +
      '<div class="scroll" id="scroll">' + summary(used) +
      (state.project.sliced ? '' : '<div class="summary"><span class="pill warn">Slice the plate to see grams and which filaments it uses</span></div>') +
      '<div class="map-head"><div>Plate filament</div><div></div><div>AFC lane</div></div>' + rows + '</div>' +
      printOptions() +
      '<div class="footer"><span class="hint">' + (UPLOAD_ONLY ? 'Applying sets each T number to its lane with SET_MAP now; AFC keeps it until a print ends.' :
        'Applying sets each T number to its lane with SET_MAP.') + '</span><div class="grow"></div>' +
      '<button class="btn" id="cancel">Cancel</button>' +
      (UPLOAD_ONLY ? '<button class="btn" id="skip" title="Upload and leave AFC\'s T map as it is">Upload without lanes</button>' :
        '<button class="btn" id="skip" title="Print with the T map AFC has now">Keep current map</button>') +
      '<button class="btn primary" id="apply"' + (blocking ? ' disabled' : '') + '>' +
        (UPLOAD_ONLY ? 'Apply and upload' : 'Apply and continue') + '</button></div>';

    document.getElementById('auto').onclick = () => { resetMapping(); render(); };
    document.getElementById('refresh').onclick = () => load(true);
    document.getElementById('cancel').onclick = () => finish('cancel');
    document.getElementById('skip').onclick = () => proceed(false);
    document.getElementById('apply').onclick = () => proceed(true);
    for (const el of document.querySelectorAll('.print-opts input[data-opt]')) {
      el.onchange = () => { u1Print[el.dataset.opt] = el.checked; };
    }
    for (const el of document.querySelectorAll('.pick-btn')) {
      el.onclick = (e) => { e.stopPropagation(); const id = +el.dataset.fil; openMenu = openMenu === id ? null : id; render(); };
    }
    for (const el of document.querySelectorAll('.opt[data-lane]')) {
      el.onclick = (e) => {
        e.stopPropagation();
        if (el.classList.contains('disabled')) return;
        mapping[+el.dataset.fil] = el.dataset.lane;
        openMenu = null;
        render();
      };
    }
  }

  function picker(f, l) {
    let html = '<div class="pick"><button class="pick-btn" data-fil="' + f.id + '">' + spool(l) +
      '<div class="txt"><div class="l1">' + (l ? esc(l.name) + ' · ' + esc(l.material || '?') : 'Pick a lane') + '</div>' +
      '<div class="l2">' + (l ? esc(l.unit.replace(/_/g, ' ')) + (l.weight > 0 ? ' · ' + grams(l.weight) + ' left' : '') + (l.extruder_index >= 0 ? ' · ' + esc(headName(l.extruder_index)) : '') + (flowK(l) ? ' · ' + esc(flowK(l)) : '') : '') + '</div></div>' +
      '<span class="caret">▾</span></button>';
    if (openMenu === f.id) {
      html += '<div class="menu">';
      for (const unit of laneUnits()) {
        html += '<div class="grp">' + icon(unitIconName(unit)) + esc(unit.name.replace(/_/g, ' ')) + ' · ' + esc(prettyType(unit.type)) + '</div>';
        for (const name of unit.lanes) {
          const x = laneByName(name);
          if (!x) continue;
          const takenBy = filaments().filter((o) => o.used && o.id !== f.id && mapping[o.id] === name).map((o) => 'T' + o.id);
          html += '<div class="opt' + (x.has_filament ? '' : ' disabled') + (mapping[f.id] === name ? ' cur' : '') + '" data-fil="' + f.id + '" data-lane="' + esc(name) + '">' +
            spool(x, true) + '<div class="txt"><div class="l1">' + esc(name) + ' · ' + esc(x.has_filament ? (x.material || '?') : 'empty') +
            '</div><div class="l2">' + esc([x.vendor, x.weight > 0 && x.has_filament ? grams(x.weight) : '',
              x.extruder_index >= 0 ? headName(x.extruder_index) : '', x.has_filament ? flowK(x) : '',
              takenBy.length ? 'used by ' + takenBy.join(', ') : ''].filter(Boolean).join(' · ')) + '</div></div>' +
            (x.maps || []).map((m) => '<span class="tchip">' + esc(m) + '</span>').join('') + '</div>';
        }
      }
      html += '</div>';
    }
    return html + '</div>';
  }

  // Toolheads that will swap filament mid-print: more than one picked lane feeds them.
  function summary(used) {
    const perHead = new Map();
    for (const f of used) {
      const l = laneByName(mapping[f.id]);
      if (!l || l.extruder_index < 0) continue;
      if (!perHead.has(l.extruder_index)) perHead.set(l.extruder_index, new Set());
      perHead.get(l.extruder_index).add(l.name);
    }
    const units = new Set(used.map((f) => (laneByName(mapping[f.id]) || {}).unit).filter(Boolean));
    let chips = '<span class="pill accent">' + units.size + ' unit' + (units.size === 1 ? '' : 's') + ' in use</span>';
    for (const [idx, set] of [...perHead.entries()].sort((a, b) => a[0] - b[0])) {
      if (set.size > 1) chips += '<span class="pill warn" title="AFC swaps filament in this toolhead during the print">' + esc(headName(idx)) + ' swaps between ' + esc([...set].join(', ')) + '</span>';
      else chips += '<span class="pill ok">' + esc(headName(idx)) + ': ' + esc([...set][0]) + '</span>';
    }
    return '<div class="summary">' + chips + '</div>';
  }

  // The U1's own print popup offers these three; they ride along with the lane map.
  const U1_OPTIONS = [
    ['flow_calibrate', 'Extrusion flow calibration', 'Calibrates flow compensation before the print'],
    ['time_lapse_camera', 'Time-lapse camera', 'Records a time-lapse of the print'],
    ['bed_level', 'Auto leveling', 'Levels the bed before the print'],
  ];

  function printOptions() {
    if (!u1Print) return '';
    return '<div class="print-opts"><span class="lbl"' + (UPLOAD_ONLY ? ' title="Written into the file, so they hold however it is started">U1 print options, saved in the file' : '>U1 print options') + '</span>' +
      U1_OPTIONS.map(([key, label, tip]) => '<label class="switch" title="' + esc(tip) + '"><input type="checkbox" data-opt="' + key + '"' +
        (u1Print[key] ? ' checked' : '') + '><span>' + esc(label) + '</span></label>').join('') + '</div>';
  }

  // An upload writes the switches into the file. Otherwise the printer gets them
  // now, as its own popup would set them.
  async function setPrintOptions() {
    if (!u1Print || UPLOAD_ONLY || u1SetFailed) return true;
    const now = (state && state.afc && state.afc.u1_print) || {};
    if (U1_OPTIONS.every(([key]) => !!now[key] === !!u1Print[key])) return true;
    let error = '';
    try {
      const data = await call('set_print_options', u1Print);
      error = (data && data.error) || '';
    } catch (e) {
      error = e.message;
    }
    if (!error) return true;
    u1SetFailed = true;
    toast('The printer did not take the U1 print options: ' + error + '. Continue again to print without them.', true);
    return false;
  }

  async function proceed(applyMap) {
    if (busy) return;
    busy = true;
    $app.classList.add('busy');
    try {
      if (applyMap) {
        const pairs = filaments().filter((f) => f.used).map((f) => ({ tool: f.id, lane: mapping[f.id] }));
        const data = await call('apply_mapping', { mapping: pairs, idle_only: UPLOAD_ONLY });
        if (data && data.error) {
          toast(data.error, true);
          return;
        }
      }
      if (!(await setPrintOptions())) return;
      await finish('continue');
    } catch (e) {
      toast(e.message, true);
    } finally {
      busy = false;
      $app.classList.remove('busy');
    }
  }

  function finish(action) {
    return call('finish_print_mapping', { action: action, u1_print: action === 'continue' ? u1Print : null })
      .catch((e) => toast(e.message, true));
  }

  document.addEventListener('click', () => {
    if (openMenu !== null) { openMenu = null; render(); }
  });

  // ------------------------------------------------------------------ start
  (function start(tries) {
    if (window.orca || tries <= 0) {
      if (window.orca) window.orca.onMessage(onAnswer);
      return load(true);
    }
    setTimeout(() => start(tries - 1), 100);
  })(30);
  if (VIEW === 'panel') {
    setInterval(() => {
      // A wait that ran out shows the form again even when the poll brings nothing new.
      if (dryStarting && Date.now() > dryStarting.until) render();
      const dryEditing = dryUnit && !((unitByName(dryUnit) || {}).dryer || {}).drying && !(dryStarting && dryStarting.unit === dryUnit);
      if (document.hidden || busy || editorDirty || dryEditing || pending.size) return;
      load(true);
    }, 5000);
  }
})();
