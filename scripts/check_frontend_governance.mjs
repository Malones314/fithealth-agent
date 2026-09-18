import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "frontend", "src");
const failures = [];
const read = (relative) => readFile(path.join(root, relative), "utf8");
const migrationStatus = JSON.parse(
  await read("frontend/domain-migration-status.json"),
);

const releases = JSON.parse(await read("docs/frontend-release-history.json"));
const eligible = releases.releases.slice(-2);
if (eligible.length < 2)
  failures.push("release history must contain at least two observations");
for (const release of eligible) {
  for (const field of [
    "version",
    "commit",
    "releasedAt",
    "legacyFallbackUsed",
    "keyE2ePassed",
    "visualRegressionPassed",
    "errorRateRegression",
  ]) {
    if (!(field in release))
      failures.push(
        `release ${release.version ?? "<unknown>"} misses ${field}`,
      );
  }
  if (release.legacyFallbackUsed !== false)
    failures.push(`${release.version}: legacy fallback was used`);
  if (release.keyE2ePassed !== true)
    failures.push(`${release.version}: key E2E did not pass`);
  if (release.visualRegressionPassed !== true)
    failures.push(`${release.version}: visual regression did not pass`);
  if (release.errorRateRegression !== false)
    failures.push(`${release.version}: error-rate regression exists`);
  if (!release.evidence)
    failures.push(`${release.version}: evidence is required`);
}

const productionFiles = [
  ".env.example",
  "fithealth_agent/runtime/frontend.py",
  "fithealth_agent/runtime/middleware.py",
  "frontend/package.json",
  "frontend/src/main.ts",
];
for (const file of productionFiles) {
  const source = await read(file);
  if (/FITHEALTH_FRONTEND_LEGACY|\/legacy\//.test(source)) {
    failures.push(`${file}: removed frontend fallback is still referenced`);
  }
}

const apiDir = path.join(src, "api");
const apiFiles = (await readdir(apiDir)).filter(
  (name) =>
    name.endsWith(".ts") &&
    !name.endsWith(".test.ts") &&
    !["client.ts", "index.ts"].includes(name),
);
const apiContractTest = await read("frontend/src/api/api-contract.test.ts");
for (const file of apiFiles) {
  const source = await read(`frontend/src/api/${file}`);
  const stem = file.slice(0, -3);
  if (!source.includes("from './client'"))
    failures.push(`api/${file}: must use the shared client`);
  if (!new RegExp(`from './${stem}'`).test(apiContractTest))
    failures.push(`api/${file}: missing call test import`);
  const apiName = source.match(/export const (\w+Api)\s*=\s*\{/)?.[1];
  if (!apiName) {
    failures.push(`api/${file}: must export a named API adapter`);
    continue;
  }
  const methods = [...source.matchAll(/^\s{2}(\w+)\([^]*?\): Promise</gm)].map(
    (match) => match[1],
  );
  if (methods.length === 0)
    failures.push(`api/${file}: methods need explicit Promise return types`);
  for (const method of methods) {
    if (!apiContractTest.includes(`${apiName}.${method}(`)) {
      failures.push(
        `api/${file}: ${apiName}.${method} is missing a call contract test`,
      );
    }
  }
}
const clientTest = await read("frontend/src/api/client.test.ts");
for (const status of [403, 409, 422, 500, 503]) {
  if (!clientTest.includes(String(status)))
    failures.push(`api/client.test.ts: missing ${status} error mapping`);
}

const domainsDir = path.join(src, "domains");
const requiredDomainFiles = [
  "state.ts",
  "controller.ts",
  "view.ts",
  "events.ts",
  "types.ts",
];
for (const entry of await readdir(domainsDir, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const domainDir = path.join(domainsDir, entry.name);
  const files = await readdir(domainDir);
  for (const required of requiredDomainFiles) {
    if (!files.includes(required))
      failures.push(`domains/${entry.name}: missing ${required}`);
  }
  if (!files.some((file) => file.endsWith(".test.ts")))
    failures.push(`domains/${entry.name}: missing behavior test`);
  if (!(entry.name in migrationStatus))
    failures.push(`domains/${entry.name}: missing migration status`);
  if (migrationStatus[entry.name]?.completed) {
    const controller = await read(
      `frontend/src/domains/${entry.name}/controller.ts`,
    );
    const view = await read(`frontend/src/domains/${entry.name}/view.ts`);
    const tests = await Promise.all(
      files
        .filter((file) => file.endsWith(".test.ts"))
        .map((file) => read(`frontend/src/domains/${entry.name}/${file}`)),
    );
    if (!/from ['"]\.\.\/\.\.\/api\//.test(controller))
      failures.push(
        `domains/${entry.name}: completed controller has no API adapter`,
      );
    if (!/(querySelector|required<)/.test(view))
      failures.push(
        `domains/${entry.name}: completed view has no owned DOM rendering`,
      );
    const controllerFactory = `create${entry.name
      .split("-")
      .map((part) => part[0].toUpperCase() + part.slice(1))
      .join("")}Controller`;
    if (!tests.join("\n").includes(controllerFactory))
      failures.push(
        `domains/${entry.name}: completed domain lacks controller behavior tests`,
      );
  }
  for (const file of files.filter((name) => name.endsWith(".ts"))) {
    const source = await read(`frontend/src/domains/${entry.name}/${file}`);
    if (/from ['"]\.\.\/\.\.\/api\/client['"]/.test(source))
      failures.push(`domains/${entry.name}/${file}: bypasses API adapters`);
    if (
      /from ['"]\.\.\/(chat|checkin|data-management|health|session|uploads|workout)\//.test(
        source,
      )
    )
      failures.push(
        `domains/${entry.name}/${file}: imports another domain's private module`,
      );
  }
}

for (const domain of Object.keys(migrationStatus)) {
  try {
    const stat = await readdir(path.join(domainsDir, domain));
    if (!stat.length)
      failures.push(`domain migration status is stale: ${domain}`);
  } catch {
    failures.push(
      `domain migration status points to missing domain: ${domain}`,
    );
  }
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else {
  console.log(
    `Frontend governance is clean: ${apiFiles.length} API adapters, ${eligible.length} stable releases.`,
  );
}
