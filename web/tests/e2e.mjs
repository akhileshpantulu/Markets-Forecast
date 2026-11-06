// End-to-end test: load the built page in Chromium, drop the fixture files,
// run the Python engine in the browser, and check the result.
//
// Usage (from web/):
//   npm run build:all
//   PYODIDE_DIR=/path/to/pyodide node tests/e2e.mjs      # local Pyodide copy
//   node tests/e2e.mjs                                     # Pyodide from the CDN
// Optional: E2E_PERF / E2E_PIPE point at other files; E2E_SHOTS=dir saves screenshots.
import http from "node:http";
import { createRequire } from "node:module";
import { readFileSync, existsSync, mkdirSync, statSync } from "node:fs";
import { extname, join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(here, "..");
const fixtures = resolve(web, "../tests/fixtures");
const PERF = process.env.E2E_PERF ?? join(fixtures, "DataTable - 2026-07-15T090000.000.xlsx");
const PIPE = process.env.E2E_PIPE ?? join(fixtures, "CostarExport - 2026-07-15T090500.000.xlsx");
const SHOTS = process.env.E2E_SHOTS;
const PYODIDE_DIR = process.env.PYODIDE_DIR;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".wasm": "application/wasm", ".json": "application/json", ".zip": "application/zip", ".whl": "application/octet-stream" };

function serve(dir, port) {
  const server = http.createServer((req, res) => {
    const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
    let file = join(dir, path);
    if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
    if (!file.startsWith(dir) || !existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream", "access-control-allow-origin": "*" });
    res.end(readFileSync(file));
  });
  return new Promise((ok) => server.listen(port, "127.0.0.1", () => ok(server)));
}

const fail = (msg) => { console.error("E2E FAILED:", msg); process.exitCode = 1; };

async function exercise(page, url, label) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(url);
  await page.getByText("Engine ready", { exact: false }).waitFor({ timeout: 180_000 });
  await page.setInputFiles("#file-performance", PERF);
  await page.setInputFiles("#file-pipeline", PIPE);
  await page.waitForFunction(() => !document.querySelector(".btn.primary")?.hasAttribute("disabled"), null, { timeout: 60_000 });
  const t0 = Date.now();
  await page.click(".btn.primary");
  await page.locator(".summary .badge").first().waitFor({ timeout: 120_000 });
  const verdict = await page.locator(".summary .badge").first().innerText();
  const fcRows = await page.locator(".panel table").first().locator("tbody tr").count();
  const err = await page.locator(".error-box").count();
  console.log(`[${label}] verdict="${verdict}" forecast rows=${fcRows} run took ${Date.now() - t0} ms`);
  if (!verdict.includes("passed")) fail(`${label}: expected a passing validation, got "${verdict}"`);
  if (err) fail(`${label}: error box shown: ${await page.locator(".error-box").first().innerText()}`);
  if (fcRows !== 6) fail(`${label}: expected 6 forecast years in the headline table, got ${fcRows}`);
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${label}-forecast.png`), fullPage: true });
  await page.getByRole("tab", { name: "Overview" }).click();
  const years = await page.locator(".panel table tbody tr").count();
  if (years < 10) fail(`${label}: expected annual rows, got ${years}`);
  for (const tab of ["History", "Backtest", "Pipeline and supply", "Checks", "Guardrails"]) {
    await page.getByRole("tab", { name: tab }).click();
    if (SHOTS) await page.screenshot({ path: join(SHOTS, `${label}-${tab.toLowerCase()}.png`), fullPage: true });
  }
  // Assumptions: change a pipeline probability, add an event, rerun.
  await page.getByRole("tab", { name: /Assumptions/ }).click();
  await page.fill("#set-pipeline-under_construction-p", "50");
  await page.dispatchEvent("#set-pipeline-under_construction-p", "change");
  await page.fill("input[name=title]", "Test event");
  await page.fill("input[name=prob]", "100");
  await page.click("form.driver-form button[type=submit]");
  const t1 = Date.now();
  await page.getByRole("button", { name: "Rerun forecast" }).click();
  await page.getByRole("button", { name: "Rerun forecast" }).waitFor({ timeout: 120_000 });
  await page.waitForFunction(() => !document.querySelector(".sticky-actions .btn.primary")?.hasAttribute("disabled"), null, { timeout: 120_000 });
  console.log(`[${label}] rerun with settings took ${Date.now() - t1} ms`);
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${label}-assumptions.png`), fullPage: true });
  await page.getByRole("tab", { name: "Forecast" }).click();
  const banner = await page.getByText("This run uses your settings").count();
  const drv = await page.getByText("Drivers in this forecast").count();
  if (!banner) fail(`${label}: no user-adjustment banner after rerun`);
  if (!drv) fail(`${label}: no driver table after adding a driver`);
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${label}-forecast-adjusted.png`), fullPage: true });
  if (errors.length) fail(`${label}: page errors: ${errors.join("; ")}`);
}

const site = await serve(join(web, "dist"), 4173);
const py = PYODIDE_DIR ? await serve(resolve(PYODIDE_DIR), 8765) : null;
const q = py ? "?pyodide=" + encodeURIComponent("http://localhost:8765/") : "";
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  await exercise(await ctx.newPage(), `http://localhost:4173/${q}`, "hosted");
  const single = pathToFileURL(join(web, "dist", "forecast-app.html")).href;
  await exercise(await ctx.newPage(), `${single}${q}`, "single-file");
} catch (e) {
  fail(e.message);
} finally {
  await browser.close();
  site.close();
  py?.close();
}
if (!process.exitCode) console.log("E2E passed");
