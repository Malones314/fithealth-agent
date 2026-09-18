import eslint from '@eslint/js';
import prettier from 'eslint-config-prettier';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'playwright-report', 'test-results'] },
  { extends: [eslint.configs.recommended, ...tseslint.configs.recommended, prettier] },
  {
    files: ['**/*.ts'],
    languageOptions: { globals: { document: 'readonly', window: 'readonly' } },
  },
);
