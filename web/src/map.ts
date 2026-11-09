// New-supply map: numbered pins on an OpenStreetMap base map (CARTO Voyager tiles), drawn on a canvas so the
// same image goes into the Excel export. Locations come from the export's Latitude/Longitude columns when present,
// otherwise from OpenStreetMap's Nominatim geocoder, only when the user clicks "Find locations". Results are cached
// in this browser.

export interface Site {
  n: number; project_id: string; name: string; rooms: number; stage: string; stage_label: string; opening: string | null; year: number | null;
  class: string | null; owner: string | null; operator: string | null; brand: string | null;
  address: string | null; city: string | null; postcode: string | null; country: string; lat: number | null; lon: number | null;
}

export const STAGE_COLOR: Record<string, string> = {
  under_construction: "#7b1e45", under_renovation: "#b0602a", final_planning: "#1d5f6b", planning: "#3d4f73", proposed: "#6b6f76", deferred: "#9a9da3",
};

const TILE = 256;
const TILE_URL = (z: number, x: number, y: number) => `https://${"abcd"[(x + y) % 4]}.basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}@2x.png`;
const ATTRIBUTION = "© OpenStreetMap contributors © CARTO";

function px(lat: number, lon: number, z: number): [number, number] {
  const s = TILE * 2 ** z;
  const r = (Math.max(Math.min(lat, 85.05), -85.05) * Math.PI) / 180;
  return [((lon + 180) / 360) * s, ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * s];
}

function loadTile(url: string, ms = 8000): Promise<HTMLImageElement | null> {
  return new Promise((ok) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    const t = setTimeout(() => ok(null), ms);
    img.onload = () => { clearTimeout(t); ok(img); };
    img.onerror = () => { clearTimeout(t); ok(null); };
    img.src = url;
  });
}

/** Draw the map. Returns the canvas and how many tiles loaded (0 when the tile server is unreachable). */
export async function renderMap(points: Site[], width = 1000, height = 560, scale = 2): Promise<{ canvas: HTMLCanvasElement; tiles: number }> {
  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(scale, scale);
  ctx.fillStyle = "#eef0ec";
  ctx.fillRect(0, 0, width, height);
  const pts = points.filter((p) => p.lat != null && p.lon != null);
  if (!pts.length) return { canvas, tiles: 0 };
  const pad = 60;
  let z = 16;
  for (; z > 3; z--) {
    const xy = pts.map((p) => px(p.lat!, p.lon!, z));
    const w = Math.max(...xy.map((q) => q[0])) - Math.min(...xy.map((q) => q[0]));
    const h = Math.max(...xy.map((q) => q[1])) - Math.min(...xy.map((q) => q[1]));
    if (w <= width - 2 * pad && h <= height - 2 * pad) break;
  }
  const xy = pts.map((p) => px(p.lat!, p.lon!, z));
  const cx = (Math.max(...xy.map((q) => q[0])) + Math.min(...xy.map((q) => q[0]))) / 2;
  const cy = (Math.max(...xy.map((q) => q[1])) + Math.min(...xy.map((q) => q[1]))) / 2;
  const x0 = cx - width / 2, y0 = cy - height / 2;
  const jobs: Promise<number>[] = [];
  for (let tx = Math.floor(x0 / TILE); tx <= Math.floor((x0 + width) / TILE); tx++) {
    for (let ty = Math.floor(y0 / TILE); ty <= Math.floor((y0 + height) / TILE); ty++) {
      const n = 2 ** z;
      if (ty < 0 || ty >= n) continue;
      const wx = ((tx % n) + n) % n;
      jobs.push(loadTile(TILE_URL(z, wx, ty)).then((img) => {
        if (!img) return 0;
        ctx.drawImage(img, tx * TILE - x0, ty * TILE - y0, TILE, TILE);
        return 1;
      }));
    }
  }
  const tiles = (await Promise.all(jobs)).reduce((a, b) => a + b, 0);
  // Pins sharing a spot (same building or postcode) fan out in a ring so every number stays visible.
  const pos = xy.map(([x, y]) => [x - x0, y - y0]);
  const groups = new Map<string, number[]>();
  pos.forEach(([x, y], i) => {
    const k = `${Math.round(x / 8)}:${Math.round(y / 8)}`;
    groups.set(k, [...(groups.get(k) ?? []), i]);
  });
  for (const ids of groups.values()) {
    if (ids.length < 2) continue;
    const r = 14 + 3 * ids.length;
    const [cx0, cy0] = pos[ids[0]];
    ids.forEach((id, j) => {
      const a = (2 * Math.PI * j) / ids.length - Math.PI / 2;
      pos[id] = [cx0 + r * Math.cos(a), cy0 + r * Math.sin(a)];
    });
  }
  // Pins: largest numbers first so low numbers sit on top.
  for (const [i, p] of [...pts.entries()].sort((a, b) => b[1].n - a[1].n)) {
    const [x, y] = pos[i];
    ctx.beginPath();
    ctx.arc(x, y, 12, 0, Math.PI * 2);
    ctx.fillStyle = STAGE_COLOR[p.stage] ?? "#7b1e45";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 12px Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(p.n), x, y + 0.5);
  }
  ctx.font = "10px Arial, sans-serif";
  const tw = ctx.measureText(ATTRIBUTION).width;
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.fillRect(width - tw - 10, height - 16, tw + 10, 16);
  ctx.fillStyle = "#333";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  ctx.fillText(ATTRIBUTION, width - 5, height - 8);
  return { canvas, tiles };
}

// ---------- geocoding (OpenStreetMap Nominatim) ----------
const CACHE = "hmf-geocode:";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function queries(s: Site): string[] {
  const j = (...xs: (string | null)[]) => xs.filter((x) => x && String(x).trim()).join(", ");
  return [...new Set([j(s.address, s.city, s.postcode, s.country), j(s.postcode, s.city, s.country), j(s.name, s.city, s.country)])].filter(Boolean);
}

function cached(q: string): { lat: number; lon: number } | null | undefined {
  try {
    const v = localStorage.getItem(CACHE + q);
    return v === null ? undefined : JSON.parse(v);
  } catch { return undefined; }
}

/** Fill lat/lon for sites without coordinates. Network calls are spaced over one second (Nominatim's usage policy). */
export async function geocode(sites: Site[], onProgress: (done: number, total: number) => void): Promise<{ found: number; failed: number }> {
  const todo = sites.filter((s) => s.lat == null || s.lon == null);
  let found = 0, failed = 0, done = 0, lastCall = 0;
  for (const s of todo) {
    let hit: { lat: number; lon: number } | null = null;
    for (const q of queries(s)) {
      const c = cached(q);
      if (c !== undefined) { hit = c; if (hit) break; continue; }
      const wait = 1100 - (Date.now() - lastCall);
      if (wait > 0) await sleep(wait);
      lastCall = Date.now();
      try {
        const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(q)}`, { headers: { "Accept-Language": "en" } });
        const js = r.ok ? await r.json() : [];
        hit = js.length ? { lat: Number(js[0].lat), lon: Number(js[0].lon) } : null;
        try { localStorage.setItem(CACHE + q, JSON.stringify(hit)); } catch { /* storage unavailable */ }
      } catch {
        hit = null;
      }
      if (hit) break;
    }
    if (hit) { s.lat = hit.lat; s.lon = hit.lon; found++; } else failed++;
    onProgress(++done, todo.length);
  }
  return { found, failed };
}

/** Apply cached locations without any network call. */
export function applyCached(sites: Site[]): number {
  let n = 0;
  for (const s of sites) {
    if (s.lat != null && s.lon != null) continue;
    for (const q of queries(s)) {
      const c = cached(q);
      if (c) { s.lat = c.lat; s.lon = c.lon; n++; break; }
    }
  }
  return n;
}
