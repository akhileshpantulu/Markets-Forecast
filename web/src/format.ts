export const pct = (x: number | null | undefined, dp = 1) =>
  x === null || x === undefined || !Number.isFinite(x) ? "" : `${(x * 100).toFixed(dp)}%`;

export const signedPct = (x: number | null | undefined, dp = 1) =>
  x === null || x === undefined || !Number.isFinite(x) ? "" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(dp)}%`;

export const num = (x: number | null | undefined, dp = 0) =>
  x === null || x === undefined || !Number.isFinite(x)
    ? ""
    : x.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });

export const pts = (x: number | null | undefined) =>
  x === null || x === undefined || !Number.isFinite(x) ? "" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)} pts`;

type Child = Node | string | null | undefined | false;

/** Small DOM helper. Attributes starting with "on" become listeners. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, unknown> = {},
  ...children: (Child | Child[])[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
    else if (k === "class") el.className = String(v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}

export function download(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
