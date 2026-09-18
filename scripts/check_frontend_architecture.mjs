import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  domainDomViolations,
  networkBypassCount,
} from "./frontend_architecture_rules.mjs";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const frontendRoot = path.join(repositoryRoot, "frontend");
const sourceRoot = path.join(frontendRoot, "src");
const failures = [];

for (const relative of [
  "frontend/src/app/runtime.ts",
  "templates/index.html",
  "scripts/frontend_assertion_inventory.py",
]) {
  try {
    await access(path.join(repositoryRoot, relative));
    failures.push(`${relative}: removed migration artifact must not exist`);
  } catch {
    // Absence is the final architecture invariant.
  }
}

async function walk(directory, pattern) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full, pattern)));
    else if (pattern.test(entry.name)) files.push(full);
  }
  return files;
}

const sourceFiles = await walk(sourceRoot, /\.tsx?$/);
for (const file of sourceFiles) {
  const relative = path.relative(sourceRoot, file).replaceAll(path.sep, "/");
  const source = await readFile(file, "utf8");
  if (
    !relative.endsWith(".test.ts") &&
    relative !== "api/client.ts" &&
    networkBypassCount(source)
  )
    failures.push(`${relative}: network access bypasses api/client.ts`);
  const lines = source.split(/\r?\n/).length;
  if (!relative.endsWith(".test.ts") && lines > 700)
    failures.push(
      `${relative}: ${lines} lines exceeds the 700-line anti-monolith limit`,
    );
}

const ownership = JSON.parse(
  await readFile(path.join(frontendRoot, "dom-ownership.json"), "utf8"),
);
const htmlFiles = [
  path.join(frontendRoot, "index.html"),
  ...(await walk(path.join(sourceRoot, "templates"), /\.html$/)),
];
const html = (
  await Promise.all(htmlFiles.map((file) => readFile(file, "utf8")))
).join("\n");
const htmlIds = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
const owners = new Map();
for (const [domain, ids] of Object.entries(ownership)) {
  for (const id of ids) {
    if (owners.has(id)) failures.push(`DOM id ${id} has two owners`);
    owners.set(id, domain);
  }
}
for (const id of htmlIds)
  if (!owners.has(id)) failures.push(`DOM id ${id} has no owner`);
for (const id of owners.keys())
  if (!htmlIds.includes(id)) failures.push(`DOM ownership has stale id ${id}`);

for (const file of sourceFiles.filter((item) =>
  item.includes(`${path.sep}domains${path.sep}`),
)) {
  const relative = path.relative(sourceRoot, file).replaceAll(path.sep, "/");
  const domain = relative.split("/")[1];
  const source = await readFile(file, "utf8");
  for (const violation of domainDomViolations(source, domain, owners))
    failures.push(
      `${relative}: accesses #${violation.id}, owned by ${violation.owner}`,
    );
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else {
  console.log(
    `Frontend architecture is final: ${sourceFiles.length} TypeScript files, ` +
      `${htmlIds.length} uniquely owned DOM ids, no compatibility monolith or network bypass.`,
  );
}
