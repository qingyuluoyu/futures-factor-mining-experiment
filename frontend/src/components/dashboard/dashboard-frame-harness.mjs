import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
export function loadTsx(source, dependencies = {}) {
  const module = { exports: {} }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  return module.exports
}
export function loadDashboardFrame() {
  const cards = loadTsx(readFileSync(new URL("../ui/card.tsx", import.meta.url), "utf8"), { "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") } })
  return loadTsx(readFileSync(new URL("./dashboard-frame.tsx", import.meta.url), "utf8"), { "@/components/ui/card": cards })
}
