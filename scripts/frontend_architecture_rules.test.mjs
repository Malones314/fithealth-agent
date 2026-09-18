import assert from "node:assert/strict";
import test from "node:test";
import {
  domainDomViolations,
  networkBypassCount,
} from "./frontend_architecture_rules.mjs";

test("detects direct, property, computed and aliased fetch calls", () => {
  for (const source of [
    'fetch("/x")',
    'window.fetch("/x")',
    'globalThis.fetch("/x")',
    'globalThis["fetch"]("/x")',
    'const request = globalThis.fetch; request("/x")',
  ])
    assert.ok(networkBypassCount(source) > 0, source);
});

test("reports cross-domain DOM access", () => {
  const owners = new Map([["workout-list", "workout"]]);
  assert.deepEqual(
    domainDomViolations(
      "document.querySelector('#workout-list')",
      "session",
      owners,
    ),
    [{ id: "workout-list", owner: "workout" }],
  );
});
