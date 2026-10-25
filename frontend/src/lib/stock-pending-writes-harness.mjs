import { readFileSync } from "node:fs"
import vm from "node:vm"
import ts from "typescript"

export function memoryStorage(initial) {
  const map = new Map(Object.entries(initial ?? {}))
  return {
    map,
    get length() { return map.size },
    key(index) { return [...map.keys()][index] ?? null },
    getItem(key) { return map.has(key) ? map.get(key) : null },
    setItem(key, value) { map.set(key, String(value)) },
    removeItem(key) { map.delete(key) },
  }
}

export function loadPendingWrites(storage, globals = {}) {
  const source = readFileSync(new URL("./stock-pending-writes.ts", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const container = { exports: {} }
  vm.runInNewContext(output, { module: container, exports: container.exports, localStorage: storage, ...globals })
  return container.exports
}
