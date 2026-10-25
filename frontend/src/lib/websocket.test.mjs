import assert from "node:assert/strict"
import test from "node:test"

import * as websocketModule from "./websocket.ts"

test("配置公网入口域名时走反代端口", () => {
  process.env.NEXT_PUBLIC_WS_PUBLIC_HOST = "quotes.example.com"
  process.env.NEXT_PUBLIC_WS_PUBLIC_PORT = "3051"
  assert.equal(typeof websocketModule.resolveWsBase, "function")
  assert.equal(
    websocketModule.resolveWsBase(
      { protocol: "https:", hostname: "quotes.example.com" },
      "",
    ),
    "wss://quotes.example.com:3051",
  )
})

test("内网/测试环境按 hostname 直连本地 backend，忽略构建时注入的公网地址", () => {
  // hostname 判定优先：即使 NEXT_PUBLIC_WS_URL 残留公网反代地址，内网 IP 也连本地 8002
  assert.equal(
    websocketModule.resolveWsBase(
      { protocol: "http:", hostname: "192.0.2.33" },
      "wss://quotes.example.com:3051",
    ),
    "ws://192.0.2.33:8002",
  )
})
