import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

test("notification initialization is independent and heavy chat is lazy", () => {
  const layout = readFileSync(new URL("../app/(main)/layout.tsx", import.meta.url), "utf8")
  const assistant = readFileSync(new URL("../components/ai/floating-assistant.tsx", import.meta.url), "utf8")
  assert.match(layout, /useNotificationPreferencesStore/)
  assert.match(layout, /Promise\.allSettled/)
  assert.doesNotMatch(layout, /getNotifySettingsApi/)
  assert.match(assistant, /dynamic\(/)
  assert.doesNotMatch(assistant, /import \{ AiChatPanel \}/)
})

test("preview output is isolated while legacy standalone is retained", () => {
  const config = readFileSync(new URL("../../next.config.ts", import.meta.url), "utf8")
  assert.match(config, /WENYUAN_BUILD_DIR/)
  assert.match(config, /WENYUAN_PREVIEW_BUILD/)
  assert.match(config, /standalone/)
})

test("private pages and preference reads wait for the verified restored session", () => {
  const layout = readFileSync(new URL("../app/(main)/layout.tsx", import.meta.url), "utf8")
  assert.match(layout, /const sessionLoaded = useAuthStore\(\(s\) => s\.loaded\)/)
  assert.match(layout, /if \(!mounted \|\| !sessionLoaded\)/)
  assert.match(layout, /if \(!accessToken \|\| !sessionLoaded \|\| !userId\) return/)
  assert.match(layout, /\[accessToken, stockMarket, sessionLoaded, userId, sessionRevision\]/)
})

test("plain chat does not advertise live tools or enable knowledge uploads", () => {
  const panel = readFileSync(new URL("../components/market/ai-chat-panel.tsx", import.meta.url), "utf8")
  assert.match(panel, /chatMode === "plain" \? PLAIN_SUGGESTIONS : SUGGESTIONS/)
  assert.match(panel, /disabled=\{knowledgeUploading \|\| chatMode === "plain"\}/)
  assert.match(panel, /chatMode === "legacy" && resumableTask/)
})
