import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "frontend",
);
const stylesDir = path.join(root, "src", "styles");
const entry = await readFile(path.join(stylesDir, "index.css"), "utf8");
const imports = [...entry.matchAll(/@import\s+'\.\/([^']+)'/g)].map(
  (match) => match[1],
);
const failures = [];

if (imports.length === 0)
  failures.push("src/styles/index.css declares no imports");
if (new Set(imports).size !== imports.length)
  failures.push("src/styles/index.css imports a file more than once");
for (const name of imports) {
  try {
    const css = await readFile(path.join(stylesDir, name), "utf8");
    if (!css.trim()) failures.push(`${name}: stylesheet is empty`);
  } catch {
    failures.push(`${name}: imported stylesheet is missing`);
  }
}

const html = await readFile(path.join(root, "index.html"), "utf8");
if (/legacy\.css|\/legacy\//.test(html))
  failures.push("frontend/index.html references the removed fallback");
if (!/src\/main\.ts/.test(html))
  failures.push("frontend/index.html no longer loads the module entry");

if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Style entry is complete: ${imports.length} non-empty modules.`);
}
