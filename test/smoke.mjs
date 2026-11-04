/**
 * Smoke test: the built bundle registers the Drive add-in lifecycle and the
 * standalone preview renders without the Geotab api.
 *
 *   npm run build && npm test
 */
import { chromium } from "playwright";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, "..", "dist");
const html = resolve(dist, "index.html");
if (!existsSync(html)) {
  console.error("dist/index.html missing — run `npm run build` first");
  process.exit(1);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 412, height: 915 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
// Serve the absolute GitHub Pages asset URLs from the local dist folder.
const base = readFileSync(html, "utf8").match(/https:\/\/[^"']+\/dist\//)?.[0];
await page.route(`${base}**`, (route) => {
  const rel = route.request().url().slice(base.length).split("?")[0];
  route.fulfill({ path: resolve(dist, rel) });
});
await page.goto("file://" + html);
await page.waitForTimeout(900);

const registered = await page.evaluate(() => typeof window.geotab?.addin?.gfAssignment === "function");
const title = await page.textContent("h2");
const lifecycle = await page.evaluate(() => Object.keys(window.geotab.addin.gfAssignment()));
await page.screenshot({ path: resolve(here, "smoke.png"), fullPage: true });
await browser.close();

const ok = registered && title?.includes("My General Foreman") && lifecycle.includes("initialize") && errors.length === 0;
console.log({ registered, title, lifecycle, errors });
console.log(ok ? "SMOKE OK" : "SMOKE FAILED");
process.exit(ok ? 0 : 1);
