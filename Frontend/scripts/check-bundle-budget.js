import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const budgetPath = path.join(__dirname, '../bundle-budget.json')
const distPath = path.join(__dirname, '../dist/assets')
const breachMarkerPath = path.join(__dirname, '../dist/BUNDLE_BREACH')

function escapeRegex(value) {
  return value.replace(/[|\\{}()[\]^$+?.]/g, '\\$&')
}

function patternToRegex(pattern) {
  return new RegExp(`^${escapeRegex(pattern).replaceAll('*', '.*')}$`)
}

function loadBudget() {
  if (!fs.existsSync(budgetPath)) {
    console.warn('bundle-budget.json not found. Skipping budget check.')
    return null
  }

  return JSON.parse(fs.readFileSync(budgetPath, 'utf-8'))
}

function loadJsAssets() {
  if (!fs.existsSync(distPath)) {
    console.error('dist/assets not found. Run build first.')
    process.exit(1)
  }

  return fs.readdirSync(distPath)
    .filter((fileName) => fileName.endsWith('.js'))
    .map((fileName) => ({
      fileName,
      sizeKb: fs.statSync(path.join(distPath, fileName)).size / 1024,
    }))
    .sort((left, right) => right.sizeKb - left.sizeKb)
}

function writeBreachMarker() {
  const markerDir = path.dirname(breachMarkerPath)
  if (!fs.existsSync(markerDir)) fs.mkdirSync(markerDir, { recursive: true })
  fs.writeFileSync(breachMarkerPath, 'true')
}

function clearBreachMarker() {
  if (fs.existsSync(breachMarkerPath)) {
    fs.unlinkSync(breachMarkerPath)
  }
}

function formatSize(sizeKb) {
  return `${sizeKb.toFixed(2)} KB`
}

function failWithSummary(lines) {
  console.error('\n--- Bundle Budget Check ---')
  lines.forEach((line) => console.error(line))
  writeBreachMarker()
  process.exit(1)
}

async function checkBudget() {
  const budget = loadBudget()
  if (!budget) return

  const jsAssets = loadJsAssets()
  if (jsAssets.length === 0) {
    failWithSummary(['No JavaScript assets found in dist/assets.'])
  }

  const trackedChunkResults = (budget.tracked_chunks || []).map((chunkBudget) => {
    const regex = patternToRegex(chunkBudget.pattern)
    const matches = jsAssets.filter((asset) => regex.test(asset.fileName))
    const largestMatch = matches[0] || null

    return {
      ...chunkBudget,
      matches,
      largestMatch,
      passed: !chunkBudget.required && matches.length === 0
        ? true
        : Boolean(largestMatch) && largestMatch.sizeKb <= chunkBudget.max_kb,
    }
  })

  const trackedPatterns = [
    ...(budget.shared_chunk_patterns || []),
    ...trackedChunkResults.map((result) => result.pattern),
  ].map(patternToRegex)

  const asyncRouteChunks = jsAssets.filter((asset) => (
    !trackedPatterns.some((regex) => regex.test(asset.fileName))
  ))

  const totalJsKb = jsAssets.reduce((sum, asset) => sum + asset.sizeKb, 0)
  const largestChunk = jsAssets[0]

  const failures = []
  if ((budget.minimum_js_chunk_count || 0) > jsAssets.length) {
    failures.push(
      `JS chunk count ${jsAssets.length} is below the required minimum of ${budget.minimum_js_chunk_count}.`
    )
  }

  if ((budget.minimum_async_route_chunk_count || 0) > asyncRouteChunks.length) {
    failures.push(
      `Async route chunk count ${asyncRouteChunks.length} is below the required minimum of ${budget.minimum_async_route_chunk_count}.`
    )
  }

  if ((budget.maximum_total_js_kb || Infinity) < totalJsKb) {
    failures.push(
      `Total JS size ${formatSize(totalJsKb)} exceeds the limit of ${formatSize(budget.maximum_total_js_kb)}.`
    )
  }

  if ((budget.maximum_single_js_chunk_kb || Infinity) < largestChunk.sizeKb) {
    failures.push(
      `Largest JS chunk ${largestChunk.fileName} (${formatSize(largestChunk.sizeKb)}) exceeds the single-chunk limit of ${formatSize(budget.maximum_single_js_chunk_kb)}.`
    )
  }

  trackedChunkResults.forEach((result) => {
    if (result.required && result.matches.length === 0) {
      failures.push(`Required chunk pattern ${result.pattern} is missing from dist/assets.`)
      return
    }

    if (!result.passed && result.largestMatch) {
      failures.push(
        `Chunk ${result.largestMatch.fileName} matched ${result.pattern} at ${formatSize(result.largestMatch.sizeKb)}, over its limit of ${formatSize(result.max_kb)}.`
      )
    }
  })

  if (failures.length > 0) {
    failWithSummary(failures)
  }

  console.log('\n--- Bundle Budget Check ---')
  console.log(`Baseline date: ${budget.baseline_date}`)
  console.log(`JS chunk count: ${jsAssets.length} (min ${budget.minimum_js_chunk_count})`)
  console.log(`Async route chunks: ${asyncRouteChunks.length} (min ${budget.minimum_async_route_chunk_count})`)
  console.log(`Total JS size: ${formatSize(totalJsKb)} (limit ${formatSize(budget.maximum_total_js_kb)})`)
  console.log(
    `Largest chunk: ${largestChunk.fileName} at ${formatSize(largestChunk.sizeKb)} (limit ${formatSize(budget.maximum_single_js_chunk_kb)})`
  )

  console.log('\nTracked chunks:')
  trackedChunkResults.forEach((result) => {
    if (!result.largestMatch) {
      console.log(`- ${result.pattern}: not emitted${result.required ? ' (required)' : ''}`)
      return
    }

    console.log(
      `- ${result.largestMatch.fileName}: ${formatSize(result.largestMatch.sizeKb)} / ${formatSize(result.max_kb)}`
    )
  })

  clearBreachMarker()
  console.log('\nBundle budget within limits.')
}

checkBudget().catch((error) => {
  console.error(error)
  writeBreachMarker()
  process.exit(1)
})
