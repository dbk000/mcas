import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(import.meta.url)

const MOCKS_DIR = path.join(process.cwd(), 'src/mocks')
const GENERATED_DIR = path.join(MOCKS_DIR, 'generated')
const SCHEMA_PATH = path.join(MOCKS_DIR, 'openapi.json')
const NATIVE_WRAPPER_PATH = path.join(GENERATED_DIR, 'native.js')

function ensureDirectory(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true })
  }
}

function resolveGeneratorBin() {
  const packageJsonPath = require.resolve('msw-auto-mock/package.json')
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'))
  const binEntry =
    typeof packageJson.bin === 'string'
      ? packageJson.bin
      : packageJson.bin?.['msw-auto-mock']

  if (!binEntry) {
    throw new Error('Unable to resolve the msw-auto-mock CLI entry point.')
  }

  return path.resolve(path.dirname(packageJsonPath), binEntry)
}

function writeNativeWrapper() {
  fs.writeFileSync(
    NATIVE_WRAPPER_PATH,
    [
      "import { setupServer } from 'msw/native'",
      "import { handlers } from './handlers'",
      '',
      'export const server = setupServer(...handlers)',
      '',
    ].join('\n'),
  )
}

function run() {
  console.log('--- MCAS OpenAPI Mock Generator ---')

  ensureDirectory(GENERATED_DIR)

  if (!fs.existsSync(SCHEMA_PATH)) {
    console.error(
      'Error: openapi.json not found in src/mocks/. Run scripts/generate_openapi.py first.',
    )
    process.exit(1)
  }

  try {
    const generatorBin = resolveGeneratorBin()

    console.log('Generating handlers from OpenAPI schema...')
    execFileSync(process.execPath, [generatorBin, SCHEMA_PATH, '-o', GENERATED_DIR], {
      stdio: 'inherit',
    })

    const handlersPath = path.join(GENERATED_DIR, 'handlers.js')
    if (!fs.existsSync(handlersPath)) {
      throw new Error('handlers.js was not found in output directory after generation.')
    }

    writeNativeWrapper()

    const stats = fs.statSync(handlersPath)
    console.log(
      `Success: Generated handlers.js (${(stats.size / 1024).toFixed(1)} KB) and refreshed native.js`,
    )
  } catch (error) {
    console.error('Generation failed:', error instanceof Error ? error.message : error)
    process.exit(1)
  }
}

run()
