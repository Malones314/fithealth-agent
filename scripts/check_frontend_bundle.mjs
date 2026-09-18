/**
 * 阶段 5 bundle 报告与预算闸门。
 *
 * 计划要求"生成 bundle 报告，并以阶段 1 的实测结果设预算；预算写入配置而不是文档中
 * 的拍脑袋数值"。预算在 `frontend/bundle-budget.json`，由本脚本在 CI 里执行。
 *
 * 比较用 gzip 后字节：用户实际下载的是压缩流，比原始体积更接近真实成本。
 *
 * `--report` 只打印报告不判定，方便在本地看清构成。
 */

import { gzipSync } from "node:zlib";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const frontendDir = path.join(repositoryRoot, "frontend");
const distDir = path.join(frontendDir, "dist");
const budgetPath = path.join(frontendDir, "bundle-budget.json");
const reportOnly = process.argv.includes("--report");

/** 把 `assets/index-*.js` 这种 glob 转成正则，只支持 `*`。 */
function toRegExp(pattern) {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, "[^/]*");
  return new RegExp(`^${escaped}$`);
}

async function listAssets(directory, prefix = "") {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      files.push(
        ...(await listAssets(path.join(directory, entry.name), relative)),
      );
    } else {
      files.push(relative);
    }
  }
  return files;
}

let budget;
try {
  budget = JSON.parse(await readFile(budgetPath, "utf8"));
} catch (cause) {
  console.error(
    `cannot read ${path.relative(repositoryRoot, budgetPath)}: ${cause.message}`,
  );
  process.exit(1);
}

let assets;
try {
  assets = await listAssets(distDir);
} catch {
  console.error("frontend/dist is missing. Run `npm run build` first.");
  process.exit(1);
}

async function sizeOf(relative) {
  const raw = await readFile(path.join(distDir, relative));
  return { raw: raw.byteLength, gzip: gzipSync(raw).byteLength };
}

const failures = [];
const rows = [];
let totalGzip = 0;
const counted = new Set();

for (const entry of budget.entries) {
  const matcher = toRegExp(entry.pattern);
  const matches = assets.filter((asset) => matcher.test(asset));
  if (matches.length === 0) {
    // 产物改名或被拆开时预算会静默失效，所以匹配不到必须报错，不能当成 0 通过。
    failures.push(`${entry.name}: no dist asset matches ${entry.pattern}`);
    continue;
  }
  let gzip = 0;
  let raw = 0;
  for (const match of matches) {
    const size = await sizeOf(match);
    gzip += size.gzip;
    raw += size.raw;
    counted.add(match);
  }
  totalGzip += gzip;
  const drift =
    entry.measuredAt > 0
      ? Math.round(((gzip - entry.measuredAt) / entry.measuredAt) * 100)
      : 0;
  rows.push({
    name: entry.name,
    files: matches.length,
    raw,
    gzip,
    limit: entry.limitBytes,
    drift,
  });
  if (gzip > entry.limitBytes) {
    failures.push(
      `${entry.name}: ${gzip} B gzip exceeds budget ${entry.limitBytes} B ` +
        `(baseline ${entry.measuredAt} B, +${drift}%)`,
    );
  }
}

if (budget.totals && totalGzip > budget.totals.limitBytes) {
  failures.push(
    `totals: ${totalGzip} B gzip exceeds budget ${budget.totals.limitBytes} B ` +
      `(baseline ${budget.totals.measuredAt} B)`,
  );
}

// 预算之外的 JS/CSS：新增一个 chunk 而忘记加预算，就会绕过这道闸门。
// 显式排除的（冻结 legacy 回退资源）不算——理由写在 bundle-budget.json 里。
const excluded = (budget.exclude?.patterns ?? []).map(toRegExp);
const unbudgeted = assets.filter(
  (asset) =>
    /\.(js|css)$/.test(asset) &&
    !counted.has(asset) &&
    !excluded.some((matcher) => matcher.test(asset)),
);
if (unbudgeted.length > 0) {
  failures.push(
    `assets outside the budget (add an entry): ${unbudgeted.join(", ")}`,
  );
}

const pad = (value, width) => String(value).padStart(width);
console.log("Bundle report (gzip):");
for (const row of rows) {
  const sign = row.drift >= 0 ? "+" : "";
  console.log(
    `  ${row.name.padEnd(18)} ${pad(row.gzip, 7)} B  ` +
      `limit ${pad(row.limit, 7)} B  raw ${pad(row.raw, 7)} B  ` +
      `${sign}${row.drift}% vs baseline  (${row.files} file${row.files === 1 ? "" : "s"})`,
  );
}
console.log(
  `  ${"TOTAL".padEnd(18)} ${pad(totalGzip, 7)} B  ` +
    `limit ${pad(budget.totals?.limitBytes ?? 0, 7)} B`,
);

if (reportOnly) {
  process.exit(0);
}

if (failures.length > 0) {
  console.error(`\n${failures.join("\n")}`);
  process.exitCode = 1;
} else {
  console.log("\nBundle sizes are within budget.");
}
