// Data-leak check for a web build. Fails when the build contains data files,
// run outputs, or test fixtures. The site must hold code only.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname, basename } from "node:path";

const root = process.argv[2] ?? "dist";
const BANNED_EXT = new Set([".xlsx", ".xlsm", ".xls", ".csv", ".parquet", ".json", ".md", ".yaml", ".yml"]);
const BANNED_NAMES = new Set(["results.json", "validation.md", "history.md", "drivers.yaml", "scenarios.yaml", "memo.md"]);
// Strings that only appear in CoStar exports or run outputs, never in code.
const BANNED_TEXT = [/Sept 20\d\d"?,\s*\d{3,}/, /"run_id"\s*:\s*"20\d\d-\d\d-\d\d-/, /Synthtown|Synthetown/];

const problems = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { walk(p); continue; }
    const ext = extname(name).toLowerCase();
    if (BANNED_EXT.has(ext) || BANNED_NAMES.has(basename(name))) problems.push(`${p}: data-type file in web build`);
    if ([".js", ".html", ".css", ".mjs"].includes(ext)) {
      const text = readFileSync(p, "utf8");
      for (const re of BANNED_TEXT) if (re.test(text)) problems.push(`${p}: matches ${re}`);
    }
  }
}
walk(root);
if (problems.length) {
  console.error("Data-leak check FAILED:\n" + problems.join("\n"));
  process.exit(1);
}
console.log(`Data-leak check passed for ${root}`);
