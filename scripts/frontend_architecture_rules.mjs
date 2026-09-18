export function networkBypassCount(source) {
  let count = [
    ...source.matchAll(
      /\bfetch\s*\(|(?:window|globalThis)\s*(?:\.\s*fetch|\[\s*['"]fetch['"]\s*\])\s*\(/g,
    ),
  ].length;
  const aliases = [
    ...source.matchAll(
      /\b(?:const|let|var)\s+(\w+)\s*=\s*(?:(?:window|globalThis)\s*(?:\.\s*fetch|\[\s*['"]fetch['"]\s*\])|fetch)\b/g,
    ),
  ].map((match) => match[1]);
  for (const alias of aliases)
    count += [...source.matchAll(new RegExp(`\\b${alias}\\s*\\(`, "g"))].length;
  return count;
}

export function domainDomViolations(source, domain, owners) {
  const ids = new Set([
    ...[
      ...source.matchAll(/getElementById(?:<[^>]+>)?\(\s*['"]([^'"]+)['"]/g),
    ].map((match) => match[1]),
    ...[
      ...source.matchAll(
        /(?:querySelector|querySelectorAll)(?:<[^>]+>)?\(\s*['"]#([A-Za-z0-9_-]+)/g,
      ),
    ].map((match) => match[1]),
  ]);
  return [...ids]
    .filter((id) => owners.get(id) && owners.get(id) !== domain)
    .map((id) => ({ id, owner: owners.get(id) }));
}
