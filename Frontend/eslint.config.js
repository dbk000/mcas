import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tailwind from 'eslint-plugin-tailwindcss'

const nodeScopedFiles = [
  'eslint.config.js',
  'playwright.config.js',
  'postcss.config.js',
  'tailwind.config.js',
  'vite.config.js',
  'scripts/**/*.{js,mjs,cjs}',
  'tests/**/*.{js,jsx}',
  'src/hooks/__tests__/**/*.{js,jsx}',
]

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'playwright-report/**',
      'test-results/**',
      'src/mocks/generated/browser.js',
      'src/mocks/generated/handlers.js',
      'src/mocks/generated/node.js',
    ],
  },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        __APP_VERSION__: 'readonly',
      },
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    settings: {
      react: {
        version: '18.2',
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      tailwindcss: tailwind,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      ...tailwind.configs.recommended.rules,
      'tailwindcss/no-unnecessary-arbitrary-value': 'error',
      'tailwindcss/enforces-shorthand': 'error',
      'tailwindcss/classnames-order': 'warn',
      'react-refresh/only-export-components': [
        'warn',
        {
          allowConstantExport: true,
          allowExportNames: ['useApi', 'useSubscription', 'useToast'],
        },
      ],
      'react/prop-types': 'off',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'react-hooks/exhaustive-deps': 'error',
      'tailwindcss/no-custom-classname': 'off',
    },
  },
  {
    files: nodeScopedFiles,
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
    rules: {
      'no-console': 'off',
    },
  },
  {
    files: ['tests/**/*.{js,jsx}', 'src/hooks/__tests__/**/*.{js,jsx}'],
    rules: {
      'no-console': 'off',
    },
  },
]
