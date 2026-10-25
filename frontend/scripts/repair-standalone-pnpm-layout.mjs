#!/usr/bin/env node
// 修补 Next.js standalone 产物在 pnpm 布局下缺失的顶层依赖。
//
// 背景（2026-10-25 CI 首次成功产物实测复现）：
//   unzip frontend-dist.zip -d app && cd app && node server.js
//   → Error: Cannot find module 'styled-jsx/package.json'
//      at next/dist/server/require-hook.js
//
// 根因：pnpm 的 node_modules 是符号链接布局。Next 用 nft 做文件追踪时，只把
// app 的直接依赖（next / react / typescript）提升到 standalone 的顶层
// node_modules/；next 自己的运行时依赖 styled-jsx、postcss、caniuse-lite、
// @next/env、@swc/helpers，以及 react-dom，仍只存在于 .pnpm 存储里。Node 从
// node_modules/next/dist/server/require-hook.js 出发向上逐级解析，永远找不到
// 它们，于是启动即 MODULE_NOT_FOUND。
//
// 内容其实都在：.pnpm/styled-jsx@5.1.6_react@19.2.6/node_modules/styled-jsx/
// 完整存在，断的只是解析路径。
//
// 用实体目录补齐而不是符号链接：CI 产物要能下载到 Windows 上解压即用，而
// Windows 创建符号链接需要 SeCreateSymbolicLinkPrivilege 或开发者模式。
//
// frontend/Dockerfile 运行期做同样的三段 COPY，镜像有同一问题；该镜像此前
// 从未被真正启动验证过。所以 Dockerfile 的 builder 阶段也调用本脚本。
//
// 用法：node scripts/repair-standalone-pnpm-layout.mjs <standalone 目录>
// 退出码：0 = 成功（含无需补齐）；1 = 发现多版本冲突或补齐后仍不完整。

import fs from 'node:fs'
import path from 'node:path'

const target = process.argv[2]

if (!target) {
  console.error('用法: node scripts/repair-standalone-pnpm-layout.mjs <standalone 目录>')
  process.exit(1)
}

// 路径写错必须当场失败。否则（CI 里）会静默跳过补齐，只靠后面的冒烟步骤兜底，
// 而那时的报错离根因已经很远了。
if (!fs.existsSync(target) || !fs.statSync(target).isDirectory()) {
  console.error(`[repair] 目标目录不存在或不是目录: ${target}`)
  process.exit(1)
}

if (!fs.existsSync(path.join(target, 'node_modules'))) {
  console.error(`[repair] 目标目录下没有 node_modules，不像 standalone 产物: ${target}`)
  process.exit(1)
}

const nm = path.join(target, 'node_modules')
const store = path.join(nm, '.pnpm')

if (!fs.existsSync(store)) {
  // 没有 .pnpm 存储说明布局本来就是平的（npm/yarn，或已是修好的产物）
  console.log('[repair] 无 .pnpm 存储，布局本就是平的，无需处理')
  process.exit(0)
}

const readVersion = (pkgDir) => {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'))
    return pkg.version ?? '?'
  } catch {
    return '?'
  }
}

// pnpm 把 @scope/name 编码成 @scope+name 作为存储目录名
const encoded = (name) => name.replace('/', '+')

const makeRecord = (name, src, storeDir) => {
  const version = readVersion(src)
  return { name, src, version, canonical: storeDir === `${encoded(name)}@${version}` }
}

// name -> 来源列表。同一个包常有多条来源：pnpm 的
// .pnpm/<pkg>@<ver>/node_modules/<dep> 本是指向存储的符号链接，被 nft
// 解引用后变成实体副本，所以会重复出现。重复不等于冲突——必须比版本号，
// 不能比路径（按路径判重会把正常产物误判成多版本，CI 直接卡死）。
const sources = new Map()
const push = (name, src, storeDir) => {
  if (!sources.has(name)) sources.set(name, [])
  sources.get(name).push(makeRecord(name, src, storeDir))
}

for (const storeDir of fs.readdirSync(store).sort()) {
  const inner = path.join(store, storeDir, 'node_modules')
  if (!fs.existsSync(inner) || !fs.statSync(inner).isDirectory()) continue
  for (const entry of fs.readdirSync(inner)) {
    const src = path.join(inner, entry)
    if (entry.startsWith('@')) {
      if (!fs.statSync(src).isDirectory()) continue
      for (const sub of fs.readdirSync(src)) {
        push(`${entry}/${sub}`, path.join(src, sub), storeDir)
      }
    } else {
      push(entry, src, storeDir)
    }
  }
}

const missing = []
for (const [name, list] of sources) {
  if (fs.existsSync(path.join(nm, name))) continue

  const versions = new Set(list.map((r) => r.version))
  if (versions.size > 1) {
    // 真有多版本时"第一个赢"会不确定地选错版本，必须显式失败而不是静默出货
    console.error(`[repair] 包 ${name} 在 .pnpm 存储里有多个版本，拒绝猜测：`)
    for (const r of list) console.error(`           ${r.version}  ${r.src}`)
    process.exit(1)
  }

  // 版本一致时优先取存储本尊，避免拿到 nft 只追了一部分的副本
  missing.push({ ...(list.find((r) => r.canonical) ?? list[0]), copies: list.length })
}
missing.sort((a, b) => a.name.localeCompare(b.name))

if (missing.length === 0) {
  console.log('[repair] 顶层 node_modules 已完整，无需补齐')
  process.exit(0)
}

for (const { name, src, copies } of missing) {
  const dst = path.join(nm, name)
  fs.mkdirSync(path.dirname(dst), { recursive: true })
  fs.cpSync(src, dst, { recursive: true })
  if (!fs.existsSync(path.join(dst, 'package.json'))) {
    console.error(`[repair] 补齐 ${name} 后仍找不到 package.json，来源可能不完整：${src}`)
    process.exit(1)
  }
  const note = copies > 1 ? `（存储中有 ${copies} 份同版本副本，取本尊）` : ''
  console.log(`[repair]   + ${name}${note}`)
}

console.log(`[repair] 共补齐 ${missing.length} 个传递依赖到顶层 node_modules`)
console.log(`[repair] 目标: ${path.resolve(target)}`)
