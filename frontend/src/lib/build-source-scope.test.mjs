import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve, relative, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const sourceRoot = resolve(root, 'src')
const styleFile = resolve(sourceRoot, 'app/globals.css')

test('native Tailwind scans application source and keeps both markets styles without reading previous builds', async () => {
  const css = readFileSync(styleFile, 'utf8')
  const declaration = css.match(/@import\s+"tailwindcss"\s+source\("([^"]+)"\);/)
  assert.ok(declaration, 'the build must explicitly bound its content sources instead of re-reading archived builds')
  assert.equal(resolve(dirname(styleFile), declaration[1]), sourceRoot)

  const tailwind = require('@tailwindcss/postcss')
  const tailwindRequire = createRequire(require.resolve('@tailwindcss/postcss'))
  const postcss = tailwindRequire('postcss')
  const result = await postcss([tailwind({ base: root, optimize: false })]).process(css, { from: styleFile })
  const files = result.messages.filter((message) => message.type === 'dependency' && /\.(?:tsx?|jsx?|mjs|html)$/.test(message.file ?? '')).map((message) => resolve(message.file))
  assert.ok(files.length > 0, 'the real native scanner must discover application content')
  for (const file of files) {
    const path = relative(sourceRoot, file)
    assert.ok(path !== '..' && !path.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(path), `unexpected content dependency: ${file}`)
  }
  for (const entry of ['components/dashboard/dashboard-frame.tsx', 'components/market/kline/kline-chart.tsx', 'components/market/stock-quote-detail.tsx']) {
    assert.ok(files.includes(resolve(sourceRoot, entry)), `missing market content: ${entry}`)
  }
  for (const selector of ['.overflow-auto', '.min-h-0', '.flex-1', '.grid-cols-6', '.lg\\:grid-cols-3']) {
    assert.ok(result.css.includes(selector), `missing application utility ${selector}`)
  }
})
