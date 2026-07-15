'use strict';

// The browser consumes precompiled state deltas; it does NOT interpret events.
// Event type/payload is used only to decide how to *animate* a change.

const state = { timeline: null, labels: null, coords: {}, lang: 'en',
                idx: 0, tokens: {}, placeMarkers: {}, territoryLayers: {}, playing: null };

const map = L.map('map', { zoomControl: true, minZoom: 4, maxZoom: 10 })
  .setView([33.5, 40], 5);
// Muted dark basemap without labels — historical overlays are the focus, and
// modern city names are noise on a 2000 BC map. Our own place labels sit on top.
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO', subdomains: 'abcd', maxZoom: 10
}).addTo(map);

const routeLayer = L.layerGroup().addTo(map);

function label(id) {
  const l = state.labels[state.lang] || {};
  return l[id] || (state.labels.en && state.labels.en[id]) || id;
}
function bc(year) { return year < 0 ? `${-year} BC` : `${year} AD`; }

// ---- state reconstruction: fold deltas 0..k across all namespaces ----
function foldState(k) {
  const world = JSON.parse(JSON.stringify(state.timeline.initial)); // {persons, territories}
  for (let i = 0; i < k; i++) {
    const ch = state.timeline.frames[i].changes;
    for (const ns of Object.keys(ch))
      for (const [id, rec] of Object.entries(ch[ns]))
        world[ns][id] = rec;
  }
  return world;
}

function statusOf(rec) {
  if (rec.alive) return 'alive';
  return rec.location ? 'dead' : 'unborn';
}

// ---- describe an event for the readout ----
function describe(f) {
  const p = f.payload, L = label;
  switch (f.type) {
    case 'PersonBorn':   return `${L(p.person)} is born at ${L(p.place)}`;
    case 'PersonDied':   return `${L(p.person)} dies at ${L(p.place)}`;
    case 'Migration':    return `${p.subjects.map(L).join(', ')} travel to ${L(p.to)}`;
    case 'CovenantMade': return `Covenant (${p.name || 'covenant'}) with ${p.parties.map(L).join(', ')}`;
    case 'TerritoryGranted': return `${L(p.territory)} granted to ${L(p.grantee)}`;
    default:             return f.event;
  }
}

// ---- render frame k, optionally animating the k-th transition ----
function render(k, animate) {
  state.idx = k;
  const world = foldState(k);
  const persons = world.persons;
  renderTerritories(world.territories, animate);

  document.getElementById('year').textContent =
    k === 0 ? 'Beginning' : bc(state.timeline.frames[k - 1].year);
  document.getElementById('event-label').textContent =
    k === 0 ? 'Move the slider to begin.' : describe(state.timeline.frames[k - 1]);
  document.getElementById('counter').textContent = `${k} / ${state.timeline.frames.length}`;
  document.getElementById('slider').value = k;

  // person roster in panel
  const ul = document.getElementById('people');
  ul.innerHTML = '';
  for (const [pid, rec] of Object.entries(persons)) {
    const st = statusOf(rec);
    const li = document.createElement('li');
    li.className = st;
    li.innerHTML = `<span class="dot ${st}"></span>${label(pid)}` +
      (rec.location ? ` <span style="color:#6b7280">· ${label(rec.location)}</span>` : '');
    ul.appendChild(li);
  }

  // routes travelled so far
  routeLayer.clearLayers();
  for (let i = 0; i < k; i++) {
    const f = state.timeline.frames[i];
    if (f.type === 'Migration' && state.coords[f.payload.from] && state.coords[f.payload.to]) {
      const cur = i === k - 1;
      L.polyline([state.coords[f.payload.from], state.coords[f.payload.to]],
        { color: '#f0a830', weight: cur ? 4 : 2.5,
          opacity: cur ? 1 : 0.65, dashArray: cur ? null : '5 6' }).addTo(routeLayer);
    }
  }

  // person tokens
  const transition = k > 0 ? state.timeline.frames[k - 1] : null;
  for (const [pid, rec] of Object.entries(persons)) {
    const st = statusOf(rec);
    let tok = state.tokens[pid];
    const latlng = rec.location ? state.coords[rec.location] : null;
    if (st === 'unborn' || !latlng) { if (tok) { map.removeLayer(tok); state.tokens[pid] = null; } continue; }
    const color = st === 'dead' ? '#6b7280' : '#4a9eff';
    if (!tok) {
      tok = L.circleMarker(latlng, { radius: 6, color: '#0e1013', weight: 1.5,
        fillColor: color, fillOpacity: 0.95 }).bindTooltip(label(pid), { direction: 'top' });
      tok.addTo(map); state.tokens[pid] = tok;
    }
    tok.setStyle({ fillColor: color });
    tok.bindTooltip(label(pid), { direction: 'top' });
    const moved = animate && transition && (transition.payload.subjects || []).includes(pid)
      && tok.getLatLng && !tok.getLatLng().equals(L.latLng(latlng));
    if (moved) tween(tok, tok.getLatLng(), L.latLng(latlng), 700);
    else tok.setLatLng(latlng);
  }
}

const TERRITORY_FILL = 0.16;

function renderTerritories(territories, animate) {
  for (const [tid, rec] of Object.entries(territories || {})) {
    const layer = state.territoryLayers[tid];
    if (!layer) continue;
    if (rec.active) {
      if (!map.hasLayer(layer)) {
        layer.addTo(map).bringToBack();     // sit beneath markers & routes
        if (animate) fadeInPoly(layer, TERRITORY_FILL);
        else layer.setStyle({ fillOpacity: TERRITORY_FILL, opacity: 0.95 });
      }
    } else if (map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
  }
}

function fadeInPoly(layer, target) {
  const t0 = performance.now(), ms = 900;
  function step(now) {
    const p = Math.min(1, (now - t0) / ms);
    layer.setStyle({ fillOpacity: target * p, opacity: 0.3 + 0.65 * p });
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function tween(marker, from, to, ms) {
  const t0 = performance.now();
  function step(now) {
    const p = Math.min(1, (now - t0) / ms);
    marker.setLatLng([from.lat + (to.lat - from.lat) * p,
                      from.lng + (to.lng - from.lng) * p]);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ---- controls ----
function go(k, animate) {
  k = Math.max(0, Math.min(state.timeline.frames.length, k));
  render(k, animate);
}
function play() {
  if (state.playing) return stop();
  document.getElementById('play').textContent = '❚❚';
  if (state.idx >= state.timeline.frames.length) go(0, false);
  state.playing = setInterval(() => {
    if (state.idx >= state.timeline.frames.length) return stop();
    go(state.idx + 1, true);
  }, 1400);
}
function stop() {
  clearInterval(state.playing); state.playing = null;
  document.getElementById('play').textContent = '▶';
}

// ---- boot ----
window.bkeLoad().then(({ timeline, labels, geo }) => {
  state.timeline = timeline; state.labels = labels;

  for (const feat of geo.features) {
    const pid = feat.properties.name_id;
    if (feat.geometry.type === 'Point') {
      const [lon, lat] = feat.geometry.coordinates;
      state.coords[pid] = [lat, lon];
      state.placeMarkers[pid] = L.circleMarker([lat, lon], { radius: 4, color: '#cbd2db',
        weight: 1, fillColor: '#9aa2ad', fillOpacity: 0.85 })
        .bindTooltip(label(pid), { permanent: true, direction: 'right',
          className: 'place-label', offset: [6, 0] }).addTo(map);
    } else if (feat.geometry.type === 'Polygon') {
      const ring = feat.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]);
      // built once, added to the map only when the territory becomes active
      state.territoryLayers[pid] = L.polygon(ring, { color: '#7bd88f', weight: 2.5,
        fillColor: '#7bd88f', fillOpacity: TERRITORY_FILL, opacity: 0.95, dashArray: '6 6' })
        .bindTooltip(label(pid), { sticky: true, className: 'place-label' });
    }
  }

  const slider = document.getElementById('slider');
  slider.max = timeline.frames.length;
  slider.addEventListener('input', e => { stop(); go(+e.target.value, false); });
  document.getElementById('prev').onclick = () => { stop(); go(state.idx - 1, true); };
  document.getElementById('next').onclick = () => { stop(); go(state.idx + 1, true); };
  document.getElementById('play').onclick = play;
  document.getElementById('lang').onchange = e => {
    state.lang = e.target.value;
    // permanent/cached tooltips are set at bind time — update them explicitly
    for (const [pid, m] of Object.entries(state.placeMarkers)) m.setTooltipContent(label(pid));
    for (const [tid, m] of Object.entries(state.territoryLayers)) m.setTooltipContent(label(tid));
    render(state.idx, false);  // panel roster + person tooltips + readout
  };

  render(0, false);
  window.__bke = { state, map, routeLayer, go, render, foldState };  // debug/test hook
});
