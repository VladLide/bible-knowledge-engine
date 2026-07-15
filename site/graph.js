'use strict';

// Knowledge-graph projection viewer. Reads the compiler's graph.json (nodes =
// entities, edges = relations) and renders it with vis-network. Same canonical
// model as the map — a different view, not a different app.

const NODE_STYLE = {
  person:    { color: '#4a9eff', shape: 'dot',      size: 16 },
  place:     { color: '#8a93a0', shape: 'square',   size: 11 },
  territory: { color: '#7bd88f', shape: 'triangle', size: 14 },
};
const FAMILY = new Set(['parent_of', 'spouse']);
const EDGE_LABEL = { parent_of: 'parent of', spouse: 'spouse', born_at: 'born at',
                     died_at: 'died at', granted: 'granted', traveled_to: 'traveled to' };

function styleFor(n) {                 // regions are type=place but styled as territory
  if (n.subtype === 'region') return NODE_STYLE.territory;
  return NODE_STYLE[n.type] || NODE_STYLE.person;
}

const g = { graph: null, labels: null, lang: 'en', nodes: null, edges: null, net: null };

function label(id) {
  const l = g.labels[g.lang] || {};
  return l[id] || (g.labels.en && g.labels.en[id]) || id;
}

function buildNodes() {
  return g.graph.nodes.map(n => {
    const s = styleFor(n);
    return { id: n.id, label: label(n.id), shape: s.shape, size: s.size,
             color: { background: s.color, border: '#0e1013' },
             font: { color: '#e8e6e1', size: 14, strokeWidth: 3, strokeColor: '#14171c' } };
  });
}

function buildEdges() {
  return g.graph.edges.map((e, i) => {
    const fam = FAMILY.has(e.rel);
    return { id: i, from: e.source, to: e.target, label: EDGE_LABEL[e.rel] || e.rel,
      arrows: e.rel === 'spouse' ? '' : 'to',
      dashes: e.rel === 'spouse' ? [4, 4] : false,
      color: { color: fam ? '#b07cd0' : '#4a5462', opacity: fam ? 0.95 : 0.6 },
      width: fam ? 2 : 1,
      font: { color: '#9aa2ad', size: 10, strokeWidth: 3, strokeColor: '#14171c',
              align: 'middle' } };
  });
}

function boot() {
  g.nodes = new vis.DataSet(buildNodes());
  g.edges = new vis.DataSet(buildEdges());
  g.net = new vis.Network(document.getElementById('graph'),
    { nodes: g.nodes, edges: g.edges },
    {
      physics: { barnesHut: { springLength: 130, gravitationalConstant: -4000 },
                 stabilization: { iterations: 200 } },
      edges: { smooth: { type: 'dynamic' } },
      interaction: { hover: true, tooltipDelay: 120 },
    });
}

document.getElementById('lang').onchange = e => {
  g.lang = e.target.value;
  g.nodes.update(buildNodes());   // relabel nodes in the new language
};

window.bkeLoad().then(({ graph, labels }) => {
  g.graph = graph; g.labels = labels;
  boot();
  window.__bkeGraph = g;   // debug/test hook
});
