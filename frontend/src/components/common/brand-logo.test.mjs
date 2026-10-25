import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import { renderToStaticMarkup } from "react-dom/server"
import { createElement } from "react"

const requireModule = createRequire(import.meta.url)
const load = (path, overrides = {}) => {
  const source = readFileSync(new URL(path, import.meta.url), "utf8")
  const module = { exports: {} }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => overrides[name] ?? requireModule(name) })
  return module.exports
}

test("Wenyuan uses a self-contained small-size SVG identity", () => {
  const svg = readFileSync(new URL("../../../public/wenyuan-mark.svg", import.meta.url), "utf8")
  assert.match(svg, /viewBox="0 0 64 64"/)
  assert.doesNotMatch(svg, /<script|<foreignObject|(?:href|src)="https?:|onload=|<image/i)
  assert.match(svg, /<path/)
})

test("shared identity has exact name and accessible decorative variants", () => {
  const brand = load("../../lib/brand.ts")
  assert.equal(brand.BRAND_NAME, "闻苑")
  const { BrandLogo } = load("./brand-logo.tsx", { "@/lib/brand": brand })
  const normal = renderToStaticMarkup(createElement(BrandLogo, { size: 40 }))
  assert.match(normal, /alt="闻苑"/)
  assert.match(normal, /width="40"/)
  assert.match(normal, /wenyuan-mark.svg/)
  const decorative = renderToStaticMarkup(createElement(BrandLogo, { decorative: true }))
  assert.match(decorative, /alt=""/)
})

test("document metadata uses the product identity and local icons", () => {
  const brand = load("../../lib/brand.ts")
  const { metadata } = load("../../app/layout.tsx", { "./globals.css": {}, "@/lib/brand": brand, "@/components/common/font-size-provider": {} })
  assert.equal(metadata.title, "闻苑")
  assert.equal(metadata.icons.icon, "/wenyuan-mark.svg")
  const apple = readFileSync(new URL("../../../public" + metadata.icons.apple, import.meta.url))
  assert.equal(apple.subarray(1, 4).toString(), "PNG")
  assert.match(metadata.description, /A股.*期货/)
})

test("all product entry points use the shared brand without legacy names", () => {
  for (const path of ["../layout/top-navbar.tsx", "../../app/(auth)/login/page.tsx", "../../app/(auth)/register/page.tsx", "../landing/landing-header.tsx", "../landing/auth/auth-dialog.tsx", "../../app/page.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8")
    assert.match(source, /BRAND_NAME/, path)
    assert.doesNotMatch(source, /金融模拟交易系统|期货 AI 终端/, path)
  }
})

test("navigation keeps the brand name visible without narrow-screen vertical wrapping", () => {
  const source = readFileSync(new URL("../layout/top-navbar.tsx", import.meta.url), "utf8")
  const title = source.match(/<span className="([^"]+)">\s*\{BRAND_NAME\}/)
  assert.ok(title)
  assert.match(title[1], /whitespace-nowrap/)
  assert.doesNotMatch(title[1], /hidden/)
})
