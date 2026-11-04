// Asserts the size budget of the built app (design D2): the initial route under 600 KB gzip, and the graph and map
// code and the detailed (50m) map outlines in their own lazy chunks. Run by `npm run build` after `vite build`; prints the numbers for the README.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const out = fileURLToPath(new URL("../../src/scgraph/api/static", import.meta.url));
const LIMIT = 600 * 1024;
const html = readFileSync(join(out, "index.html"), "utf8");
const assets = readdirSync(join(out, "assets"));
const gz = (file) => gzipSync(readFileSync(join(out, "assets", file))).length;

// The initial route: what index.html loads (entry script, its preloaded imports, styles).
const initial = [...html.matchAll(/(?:src|href)="\/assets\/([^"]+)"/g)].map((m) => m[1]).filter((f) => /\.(js|css)$/.test(f));
const initialBytes = initial.reduce((sum, f) => sum + gz(f), 0);
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

// Chunk file names are left out of the search: the entry's preload map names the lazy chunks without holding their code.
const code = (f) => readFileSync(join(out, "assets", f), "utf8").replace(/assets\/[\w.-]+\.js/g, "");
const chunkWith = (needle) => assets.filter((f) => f.endsWith(".js") && code(f).includes(needle));
const graph = chunkWith("cytoscape");
const map = chunkWith('{"countries":'); // the bundled world-atlas shapes
const detail = assets.filter((f) => f.startsWith("countries-50m") && f.endsWith(".js")); // loaded on zoom
const problems = [];
if (detail.length === 0) problems.push("no chunk of its own holds the 50m map outlines");
if (detail.some((c) => initial.includes(c))) problems.push("the 50m map outlines are in the initial route");
if (!map.some((c) => !detail.includes(c))) problems.push("the 50m map outlines share the map code's chunk");
if (initialBytes > LIMIT) problems.push(`initial route is ${kb(initialBytes)} gzip, over ${kb(LIMIT)}`);
for (const [name, chunks] of [["graph (cytoscape)", graph], ["map (d3-geo)", map]]) {
  if (chunks.length === 0) problems.push(`no chunk holds the ${name} code`);
  if (chunks.some((c) => initial.includes(c))) problems.push(`the ${name} code is in the initial route`);
}
console.log(`initial route: ${kb(initialBytes)} gzip (${initial.join(", ")})`);
for (const f of assets.filter((f) => f.endsWith(".js") && !initial.includes(f)).sort((a, b) => gz(b) - gz(a)).slice(0, 8)) {
  console.log(`  lazy ${f}: ${kb(gz(f))} gzip`);
}
if (problems.length) {
  console.error(`bundle check failed:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log("bundle check ok");
