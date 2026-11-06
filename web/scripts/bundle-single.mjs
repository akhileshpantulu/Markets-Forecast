// Copy the single-file build into the hosted site so it can be downloaded
// from the page and used locally (web/dist/forecast-app.html).
import { copyFileSync, existsSync } from "node:fs";
const src = "dist-single/index.html";
if (!existsSync(src)) throw new Error(`${src} missing; run npm run build:single first`);
copyFileSync(src, "dist/forecast-app.html");
console.log("copied", src, "-> dist/forecast-app.html");
