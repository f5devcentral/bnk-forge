import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

// D-020: regex matching Tailwind raw-palette color classes on chrome.
// Catches `text-red-500`, `bg-emerald-50`, `border-amber-200`, `from-blue-600`, etc.
// Tokens (`bg-card`, `text-success`, `bg-primary/10`, …) are NOT matched.
// See: docs/adr/D-020-f5-design-system-token-discipline.md
const RAW_PALETTE_REGEX =
  '\\b(bg|text|border|ring|from|to|via|outline|divide|placeholder|caret|fill|stroke|accent|shadow|decoration)-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-(50|100|200|300|400|500|600|700|800|900|950)\\b'

// Data-viz allowlist (ADR §3 / resolved decisions §6):
// reactflow nodes/edges, recharts series, syntax highlighters, terminal themes
// MAY use a fixed palette confined to those data-viz surfaces.
const DATA_VIZ_ALLOWLIST_FILES = [
  // reactflow / @xyflow node renderers
  'src/components/k8s/ResourceTopologyGraph.tsx',
  'src/components/modules/DependencyGraphViewer.tsx',
  'src/components/deployments/DeploymentPipeline.tsx',
  // recharts series colors
  'src/pages/BenchmarkRunDetail.tsx',
  'src/pages/BenchmarkCompareTab.tsx',
  // syntax highlighter / Monaco editor themes
  'src/components/k8s/F5iRuleViewer.tsx',
  'src/components/k8s/YamlEditor.tsx',
  // xterm.js terminal themes
  'src/components/k8s/PodExecTerminal.tsx',
  'src/components/dpu/DpuTerminalDialog.tsx',
]

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [
      ...tseslint.configs.recommended,
    ],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
        },
      ],
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@tanstack/react-query',
              importNames: ['useMutation'],
              message:
                "Use useAppMutation from '@/hooks/lib/useAppMutation' so failures get a default error toast.",
            },
          ],
        },
      ],
      // D-020: raw Tailwind palette colors on chrome are forbidden — use design tokens
      // (bg-card, text-foreground, text-success, bg-primary/10, etc.).
      // Phase 2 (WO-6) sweep complete across all component areas — promoted to
      // `error` and CI-blocking. The data-viz allowlist below is the only escape
      // hatch (charts/graphs/terminals/syntax-highlighters keep fixed palettes).
      'no-restricted-syntax': [
        'error',
        {
          selector: `Literal[value=/${RAW_PALETTE_REGEX}/]`,
          message:
            "D-020: use design tokens (bg-card, text-foreground, text-success, bg-primary/10, …) instead of raw Tailwind palette colors. See docs/adr/D-020-f5-design-system-token-discipline.md",
        },
        {
          selector: `TemplateElement[value.raw=/${RAW_PALETTE_REGEX}/]`,
          message:
            "D-020: use design tokens (bg-card, text-foreground, text-success, bg-primary/10, …) instead of raw Tailwind palette colors. See docs/adr/D-020-f5-design-system-token-discipline.md",
        },
      ],
    },
  },
  {
    files: ['src/hooks/lib/useAppMutation.ts'],
    rules: {
      'no-restricted-imports': 'off',
    },
  },
  // D-020: data-viz allowlist — these files render charts, graphs, syntax
  // highlighters, or terminals where a fixed categorical palette is allowed.
  {
    files: DATA_VIZ_ALLOWLIST_FILES,
    rules: {
      'no-restricted-syntax': 'off',
    },
  },
  // Test files: assertions on class strings are allowed
  {
    files: ['**/__tests__/**/*.{ts,tsx}', '**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': 'off',
    },
  },
)
