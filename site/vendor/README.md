# Vendored front-end libraries

These files are committed on purpose so the site has **no runtime dependency on
any third-party server** for code. Builds are reproducible and work offline; a
CDN outage or a compromised package can't alter what runs in a visitor's browser.

| File | Library | Version | Source |
|------|---------|---------|--------|
| `leaflet.js`, `leaflet.css` | [Leaflet](https://leafletjs.com/) | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/ |
| `vis-network.min.js`, `vis-network.min.css` | [vis-network](https://visjs.github.io/vis-network/) | 9.1.9 | https://unpkg.com/vis-network@9.1.9/ |

## Updating

Re-download the exact version, replace the file, bump the version above, and
verify both pages still render (headless-Chrome screenshot pass). Example:

```bash
curl -sSL -o leaflet.js https://unpkg.com/leaflet@<version>/dist/leaflet.js
```

## Not vendored

Map **tiles** (CARTO basemap, OpenStreetMap) are fetched at runtime — they are
data, not code, and vendoring a whole basemap is out of scope. Worst case if a
tile host is unreachable: the background doesn't draw, but all overlays
(markers, routes, territory) still render from local data.
