import { defineConfig } from 'vite';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const templateRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'src/templates');

function htmlPartials() {
  return {
    name: 'fithealth-html-partials',
    transformIndexHtml(html: string): string {
      return html.replace(
        /<!--#include virtual="\/src\/templates\/([a-z-]+\.html)" -->/g,
        (_match, name: string) => readFileSync(path.join(templateRoot, name), 'utf8'),
      );
    },
  };
}

const apiPrefixes = [
  '/analyze_food',
  '/chat',
  '/checkins',
  '/data',
  '/health',
  '/logout',
  '/memories',
  '/plans',
  '/profile',
  '/records',
  '/session',
  '/settings',
  '/upload_fit',
  '/upload_health',
  '/upload_plan',
  '/workout_state',
];

export default defineConfig({
  plugins: [htmlPartials()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      apiPrefixes.map((prefix) => [prefix, { target: 'http://127.0.0.1:9999' }]),
    ),
  },
});
