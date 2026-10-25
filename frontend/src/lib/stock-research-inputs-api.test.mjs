import assert from "node:assert/strict"
import test from "node:test"

export const fixture = {
  inputs_version: "stock-research-inputs-v1", market: "stock", query: { start_date: "2026-09-01", end_date: "2026-09-03", factor_ids: ["pb"], mktcap_neutral: false, weight: "equal" }, status: "potential_inputs",
  prices: { rows: 30, valid_close_rows: 30, invalid_rows: 0, securities: 10, observed_dates: 3, duplicate_groups: 0 },
  fields: [{ field: "pb", source: "valuation", unit: "ratio", source_rows: 30, finite_values: 20, usable_values: 20, missing_values: 10, nonfinite_values: 0, excluded_nonpositive: 0, rule: "finite_strictly_positive" }],
  factors: [{ factor_id: "pb", label: "市净率", required_fields: ["pb"], minimum_observed_dates: 1, status: "potential_inputs", reasons: [] }], auxiliary_missing: [], adjustment: "forward_adjusted_unversioned", timezone: "Asia/Shanghai", frequency: "daily",
  point_in_time_certified: false, source_calls: 0, model_calls: 0, read_at: "2026-10-20T00:00:00+00:00", transaction: "postgresql_repeatable_read_readonly", limitations: ["不保证计算成功或PIT"] }

test("research input client validates count partitions, dependency identity and honest status", async () => {
  const { researchInputsSchema } = await import("./stock-research-inputs-api.ts")
  assert.deepEqual(researchInputsSchema.parse(fixture), fixture)
  for (const change of [{ point_in_time_certified: true }, { status: "missing_inputs" }, { fields: [] },
    { fields: [{ ...fixture.fields[0], usable_values: 0 }] }, { factors: [] },
    { prices: { ...fixture.prices, invalid_rows: 2 } }, { fields: [{ ...fixture.fields[0], unit: "percent" }] },
    { source_calls: 1 }, { query: { ...fixture.query, factor_ids: ["roe"] } }])
    assert.equal(researchInputsSchema.safeParse({ ...fixture, ...change }).success, false)
})

test("input request binds token cancellation no-store and query echo without retries", async () => {
  const { getStockResearchInputs } = await import("./stock-research-inputs-api.ts")
  let calls = 0, request
  const controller = new AbortController()
  globalThis.fetch = async (url, options) => { calls++; request = { url, options }; return Response.json(fixture) }
  assert.deepEqual(await getStockResearchInputs(fixture.query, "captured-token", controller.signal), fixture)
  assert.match(request.url, /\/api\/stock\/research\/inputs\?/)
  assert.equal(request.options.headers.Authorization, "Bearer captured-token")
  assert.equal(request.options.cache, "no-store")
  controller.abort(); assert.equal(request.options.signal.aborted, true)
  await assert.rejects(getStockResearchInputs(fixture.query, "captured-token", controller.signal))
  assert.equal(calls, 1)
  globalThis.fetch = async () => { calls++; return Response.json({ ...fixture, query: { ...fixture.query, end_date: "2026-09-04" } }) }
  await assert.rejects(getStockResearchInputs(fixture.query, "token"), /回显/)
})

test("input check does not turn source failure or old deployment into empty valid data", async () => {
  const { getStockResearchInputs } = await import("./stock-research-inputs-api.ts")
  globalThis.fetch = async () => Response.json({ detail: "不可泄露的后端正文" }, { status: 404, headers: { "X-Request-ID": "a".repeat(32) } })
  await assert.rejects(getStockResearchInputs(fixture.query, "token"), (error) => /尚未部署/.test(error.message) && error.request_id === "a".repeat(32) && !error.message.includes("不可泄露"))
  await assert.rejects(getStockResearchInputs({ ...fixture.query, start_date: "20260901" }, "token"))
})
