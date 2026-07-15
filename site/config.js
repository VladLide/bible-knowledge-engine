'use strict';
// Where the site reads its data: repo A's published data API (GitHub Pages).
// The site is just one client of this API — third parties read the same URLs.
// Override for local testing with ?data=<base>  (e.g. ?data=/public/v1).
window.BKE_DATA_BASE =
  new URLSearchParams(location.search).get('data') ||
  'https://vladlide.github.io/bible-knowledge-engine/v1';

// Load the manifest, then fetch every artifact it lists. Timeline eras are
// concatenated here (eager); lazy per-era loading is a future optimisation for
// when there are many chunks — the format already supports it.
window.bkeLoad = async function () {
  const base = window.BKE_DATA_BASE;
  const get = p => fetch(`${base}/${p}`).then(r => {
    if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`);
    return r.json();
  });
  const manifest = await get('manifest.json');
  const [initial, labels, geo, graph] = await Promise.all([
    get(manifest.timeline.initial), get(manifest.labels),
    get(manifest.geometry), get(manifest.graph),
  ]);
  const eras = await Promise.all(manifest.timeline.eras.map(e => get(e.file)));
  return {
    manifest,
    timeline: { model: manifest.model, years: manifest.years, initial, frames: eras.flat() },
    labels, geo, graph,
  };
};
