/**
 * 阶段 4 共享组件闸门。
 *
 * 计划要求"用静态检查限制领域模块自行创建共享弹窗、toast 和空态结构"。这里检查的
 * 是**结构性**违规，不是文本相似度：
 *   - 领域/runtime 不得自己操作模态框的 `show` class、`aria-hidden` 或 body overflow；
 *   - 不得自己拼 `msg system` 通知条或 `data-empty` / `workout-empty` 空态；
 *   - 不得直接调用 `window.confirm`，或自己注册 Escape 关闭逻辑；
 *   - 不得自行注册 drag/drop 监听（拖拽深度计数只有一份实现）；
 *   - `innerHTML` 只允许出现在 shared/sanitize.ts 里。
 *
 * `src/components/` 自己是这些行为的实现处，因此豁免。
 */

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const sourceRoot = path.join(repositoryRoot, "frontend", "src");

const MODAL_ROOTS = [
  "health-modal",
  "checkin-modal",
  "data-modal",
  "activity-picker-modal",
];

const CHECKS = [
  {
    id: "modal-visibility",
    // 只针对模态框根节点：非模态的 `.show`（内联提示、预览折叠、工具栏）不在
    // Modal 的职责范围内，阶段 4 不改它们的结构。
    test: (source) =>
      MODAL_ROOTS.some((id) => {
        const variable = new RegExp(
          `\\$?${id.replace(/-/g, "[-A-Za-z]*")}[A-Za-z]*\\.classList\\.(add|remove|toggle)\\('show'`,
        );
        return variable.test(source);
      }) ||
      /\$(health|checkin|data|activityPicker)Modal\.classList\.(add|remove|toggle)\('show'/.test(
        source,
      ),
    message: "toggles a modal `show` class directly; use components/modal",
  },
  {
    id: "modal-aria-hidden",
    test: (source) => /Modal\.setAttribute\('aria-hidden'/.test(source),
    message: "sets a modal's aria-hidden directly; use components/modal",
  },
  {
    id: "body-scroll-lock",
    test: (source) => /document\.body\.style\.overflow/.test(source),
    message: "locks body scrolling directly; use components/modal",
  },
  {
    id: "toast-structure",
    test: (source) => /'msg system|"msg system|msg system'/.test(source),
    message: "builds a notification row by hand; use components/toast",
  },
  {
    id: "empty-state-structure",
    test: (source) => /class="(data-empty|workout-empty)"/.test(source),
    message: "builds an empty state by hand; use components/empty-state",
  },
  {
    id: "raw-confirm",
    test: (source) => /window\.confirm\s*\(/.test(source),
    message: "calls window.confirm directly; use components/confirm-dialog",
  },
  {
    id: "escape-handling",
    test: (source) => /key\s*===\s*'Escape'/.test(source),
    message: "implements Escape-to-close; components/modal already does",
  },
  {
    id: "drag-and-drop",
    test: (source) =>
      /addEventListener\('(dragenter|dragover|dragleave|drop)'/.test(source),
    message: "registers drag/drop listeners; use components/file-dropzone",
  },
  {
    id: "inner-html",
    test: (source) => /\.innerHTML\s*=/.test(source),
    message: "assigns innerHTML; route rich text through shared/sanitize",
  },
];

/** components/ 是这些行为的实现处；shared/sanitize 是 innerHTML 的唯一入口。 */
function exemptions(relative) {
  if (relative.startsWith("components/"))
    return CHECKS.map((check) => check.id);
  if (relative === "shared/sanitize.ts") return ["inner-html"];
  if (relative === "app/shell.ts") return ["modal-visibility", "drag-and-drop"];
  return [];
}

const violations = [];

async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await walk(file);
      continue;
    }
    if (!/\.tsx?$/.test(entry.name) || entry.name.endsWith(".test.ts"))
      continue;
    const relative = path.relative(sourceRoot, file).replaceAll(path.sep, "/");
    const allowed = exemptions(relative);
    const source = await readFile(file, "utf8");
    for (const check of CHECKS) {
      if (allowed.includes(check.id)) continue;
      if (check.test(source))
        violations.push(`${relative}: ${check.message} [${check.id}]`);
    }
  }
}

await walk(sourceRoot);

if (violations.length > 0) {
  console.error(violations.join("\n"));
  process.exitCode = 1;
} else {
  console.log(
    "Shared component boundaries are clean; no innerHTML debt remains.",
  );
}
