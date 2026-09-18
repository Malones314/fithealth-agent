import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "frontend",
  "src",
);
const violations = [];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) await walk(file);
    else if (/\.tsx?$/.test(entry.name)) {
      const relative = path.relative(root, file).replaceAll(path.sep, "/");
      const source = await readFile(file, "utf8");
      const directNetwork =
        /\bfetch\s*\(|(?:window|globalThis)\s*(?:\.\s*fetch|\[\s*['"]fetch['"]\s*\])\s*\(/.test(
          source,
        );
      if (relative !== "api/client.ts" && directNetwork)
        violations.push(`${relative}: direct fetch`);
      if (
        relative.startsWith("api/") &&
        /\b(document|window|HTMLElement|DOMParser)\b/.test(source)
      )
        violations.push(`${relative}: API imports DOM`);
      if (
        relative.startsWith("shared/") &&
        /from ['"].*\b(domains|components|app)\//.test(source)
      )
        violations.push(`${relative}: shared imports app/domain/component`);
      if (
        relative.startsWith("domains/") &&
        /from ['"]\.\.\/\.\.\/api\/client['"]/.test(source)
      )
        violations.push(`${relative}: domain bypasses API adapter`);
      if (
        relative.startsWith("domains/") &&
        /from ['"]\.\.\/(chat|checkin|data-management|health|session|uploads|workout)\//.test(
          source,
        )
      )
        violations.push(`${relative}: cross-domain private import`);
    }
  }
}

await walk(root);
if (violations.length) {
  console.error(violations.join("\n"));
  process.exitCode = 1;
} else {
  console.log("Frontend dependency boundaries are clean.");
}
