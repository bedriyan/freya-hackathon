// node test/run.mjs   -> plays the scenarios below against the Worker (src/worker.js + src/data.js) with fake Durable Objects.
// Written for the example bank (local-simulation/blind/s02). After refilling data.js, edit the constants in the first block.
import DATA from "../src/data.js";
import { makeApp } from "./fake.mjs";

// ---- the example bank's values the scenarios use
const A = "S02-01", B = "S02-02";
const ID = { [A]: [["company_number", "SC418802"], ["founded_year", "2012"]], [B]: [["company_number", "09317745"], ["founded_year", "2014"]] };
const SHORT = ["company_number", "SC4188", "incomplete_number"];            // wrong shape -> hint, not counted
const PROMISE = { product_id: "p1", amount: 1840, date: "2026-12-22", part_payment: false };
const CLOSE = { outcome: "commitment", identity_verified: true };
const BAD_ENUM = ["transfer_to_agent", { queue: "the_moon" }];
const MAX = DATA.identity.maxWrong;

let app = makeApp(), failed = 0;
const call = async (path, body, method = "POST", a = app) => {
  const r = await a.fetch(new Request("https://mock.test" + path, { method, headers: { "content-type": "application/json" }, body: method === "GET" ? undefined : JSON.stringify(body ?? {}) }));
  return { status: r.status, body: await r.json(), headers: r.headers };
};
const svc = (name, cid, args = {}, a = app) => call(DATA.basePath + DATA.services[name].path, { customer_id: cid, ...args }, "POST", a);
const verify = (cid, field, answer, a = app) => svc("verify_identity", cid, { field, answer }, a);
const ledger = (cid, a = app) => call(`${DATA.basePath}/_ledger/${cid}`, null, "GET", a);
const eq = (got, want, what) => { if (JSON.stringify(got) !== JSON.stringify(want)) throw new Error(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`); };
async function scenario(name, fn) {
  app = makeApp();
  try { await fn(); console.log("PASS", name); } catch (e) { failed++; console.log("FAIL", name, "\n     ", e.message); }
}

await scenario("health", async () => {
  const r = await call("/health", null, "GET");
  eq([r.status, r.body.ok, r.body.customers, typeof r.body.version], [200, true, Object.keys(DATA.customers).length, "string"], "health");
  eq(r.headers.get("access-control-allow-origin"), "*", "cors");
  eq((await app.fetch(new Request("https://mock.test/v1/get_account", { method: "OPTIONS" }))).status, 204, "preflight");
});

await scenario("happy path: identity, account, promise, close, ledger", async () => {
  let r = await verify(A, ...ID[A][0]); eq(r.body, { ok: true, verified: true, wrong_answers_left: MAX, all_verified: false }, "first answer");
  r = await svc("get_account", A); eq(r.body, { ok: false, error: "identity not verified" }, "account before identity");
  r = await verify(A, ...ID[A][1]); eq(r.body, { ok: true, verified: true, wrong_answers_left: MAX, all_verified: true }, "second answer");
  r = await svc("get_account", A); eq([r.status, r.body.ok, r.body.products.length, r.body.products[0].product_id], [200, true, DATA.customers[A].products.length, "p1"], "account");
  r = await svc("create_promise", A, PROMISE); eq([r.status, r.body], [200, { ok: true, promise_id: "pr1" }], "promise");
  r = await svc("create_promise", A, PROMISE); eq(r.body, { ok: false, error: "this product already has a promise" }, "second promise");
  r = await svc("close_call", A, CLOSE); eq([r.status, r.body], [200, { ok: true }], "close");
  r = await svc("add_note", A, { product_id: "p1", reason: "declined" }); eq(r.body.ok, false, "after close");
  const l = (await ledger(A)).body;
  eq(l.promises, [{ promise_id: "pr1", type: DATA.customers[A].products[0].type, ...PROMISE }], "ledger promises");
  eq([l.outcome, l.reason, l.wrong, l.hinted, l.notes, l.calls.length, l._state.closed], ["commitment", null, 0, false, [], 8, true], "ledger");
  eq(l.calls[0], { n: 1, tool: "verify_identity", args: { field: ID[A][0][0], answer: ID[A][0][1] }, status: 200, result: { ok: true, verified: true, wrong_answers_left: MAX, all_verified: false } }, "ledger call 1");
});

await scenario("answers are compared without case, spaces and hyphens", async () => {
  const r = await verify(A, "company_number", " sc 418-802 "); eq(r.body.verified, true, "normalised answer");
});

await scenario("wrong answers up to the limit, then the check has ended", async () => {
  for (let i = 1; i <= MAX; i++) { const r = await verify(A, "founded_year", "1900"); eq(r.body, { ok: true, verified: false, wrong_answers_left: MAX - i, all_verified: false }, "wrong " + i); }
  let r = await verify(A, ...ID[A][0]); eq([r.status, r.body], [200, { ok: false, error: "the identity check has ended after too many wrong answers" }], "after the limit");
  r = await svc("get_account", A); eq(r.body, { ok: false, error: "identity not verified" }, "account stays refused");
  eq((await ledger(A)).body.wrong, MAX, "ledger wrong");
});

await scenario("wrong shape gets a hint and is not counted", async () => {
  let r = await verify(A, SHORT[0], SHORT[1]); eq(r.body, { ok: true, verified: false, wrong_answers_left: MAX, all_verified: false, hint: SHORT[2] }, "hint");
  r = await verify(A, SHORT[0], SHORT[1]); eq(r.body.wrong_answers_left, MAX, "second hint");
  const l = (await ledger(A)).body; eq([l.wrong, l.hinted], [0, true], "ledger");
});

await scenario("unknown customer, unknown service, bad body", async () => {
  let r = await svc("get_account", "nobody"); eq([r.status, r.body], [404, { ok: false, error: "unknown customer_id" }], "unknown customer");
  r = await call("/v1/get_account", {}); eq(r.status, 404, "no customer_id");
  r = await call("/v1/_ledger/nobody", null, "GET"); eq(r.status, 404, "ledger of unknown customer");
  r = await call("/v1/open_vault", { customer_id: A }); eq([r.status, r.body], [404, { ok: false, error: "unknown tool open_vault" }], "unknown service");
  const raw = await app.fetch(new Request("https://mock.test/v1/get_account", { method: "POST", body: "{not json" })); eq(raw.status, 400, "bad json");
  r = await call("/nothing", null, "GET"); eq(r.status, 404, "not found");
});

await scenario("validation: enum, missing field, number, date", async () => {
  let r = await svc(BAD_ENUM[0], A, BAD_ENUM[1]);
  eq([r.status, r.body], [400, { ok: false, error: "queue must be one of ['insolvency_team', 'payment_tracing', 'business_support', 'sme_servicing', 'fraud_ops']" }], "enum");
  r = await verify(A, "mothers_shoe_size", "42"); eq(r.status, 400, "identity field outside enum");
  r = await svc("close_call", A, { identity_verified: false }); eq([r.status, r.body], [400, { ok: false, error: "missing field outcome" }], "missing");
  r = await svc("create_promise", A, { ...PROMISE, amount: "lots" }); eq([r.status, r.body], [400, { ok: false, error: "amount must be a number" }], "number");
  r = await svc("create_promise", A, { ...PROMISE, date: "22/12/2026" }); eq([r.status, r.body], [400, { ok: false, error: "date must be a date as YYYY-MM-DD" }], "date");
  r = await svc("schedule_callback", A, { date: "2026-02-30", time: "10:00", arranged_with: "named_contact" }); eq(r.status, 400, "impossible date");
  const l = (await ledger(A)).body; eq([l.transfers, l.callbacks, l.promises, l.outcome, l.calls.length], [[], [], [], null, 6], "nothing recorded, every call logged");
});

await scenario("state is kept between requests and separate between customers", async () => {
  await verify(A, "founded_year", "1900");
  await verify(A, ...ID[A][0]);
  for (const [f, v] of ID[B]) await verify(B, f, v);
  let r = await svc("get_account", B); eq(r.body.ok, true, "B verified");
  r = await svc("get_account", A); eq(r.body.ok, false, "A not verified");
  r = await svc("transfer_to_agent", B, { queue: "fraud_ops" }); eq(r.body, { ok: true }, "transfer");
  const [la, lb] = [(await ledger(A)).body, (await ledger(B)).body];
  eq([la.wrong, la.verified, la.transfers, la.calls.length], [1, { company_number: true }, [], 3], "ledger A");
  eq([lb.wrong, lb.verified, lb.transfers, lb.calls.length], [0, { company_number: true, founded_year: true }, [{ queue: "fraud_ops" }], 4], "ledger B");
  eq([la.case, lb.case], [A, B], "ledger case");
});

await scenario("reset clears one customer only", async () => {
  await verify(A, "founded_year", "1900"); await verify(B, "founded_year", "1900");
  let r = await call(`/v1/_reset/${A}`, {}); eq([r.status, r.body.ok], [200, true], "reset");
  const [la, lb] = [(await ledger(A)).body, (await ledger(B)).body];
  eq([la.wrong, la.calls.length, lb.wrong, lb.calls.length], [0, 0, 1, 1], "after reset");
  r = await verify(A, "founded_year", "1900"); eq(r.body.wrong_answers_left, MAX - 1, "counts from zero again");
});

await scenario("a service fault answers 500 the first N times and records nothing", async () => {
  const F = Object.keys(DATA.customers).find((c) => Object.keys(DATA.customers[c].faults || {}).length);
  if (!F) return;
  const [tool, n] = Object.entries(DATA.customers[F].faults)[0];
  for (const [f, v] of Object.entries(DATA.customers[F].identity)) await verify(F, f, v);
  const args = tool === "create_promise" ? PROMISE : {};
  for (let i = 0; i < n; i++) eq((await svc(tool, F, args)).status, 500, "fault " + (i + 1));
  eq((await svc(tool, F, args)).status, 200, "then works");
  const l = (await ledger(F)).body; eq([l.failures, l.promises.length], [{ [tool]: n }, tool === "create_promise" ? 1 : 0], "ledger");
});

await scenario("session rules: a new call starts by itself after close / idle time (data.session)", async () => {
  const data = { ...structuredClone(DATA), session: { idleResetSeconds: 600, resetAfterCloseSeconds: 120 } };
  const a = makeApp(data), realNow = Date.now; let t = realNow();
  Date.now = () => t;
  try {
    await verify(A, "founded_year", "1900", a); await svc("close_call", A, { outcome: "no_contact", reason: "wrong_number", identity_verified: false }, a);
    t += 30_000; eq((await verify(A, ...ID[A][0], a)).body.ok, false, "still closed after 30 s");
    t += 121_000; eq((await verify(A, ...ID[A][0], a)).body, { ok: true, verified: true, wrong_answers_left: MAX, all_verified: false }, "fresh call after close + 120 s");
    let l = (await ledger(A, a)).body; eq([l.calls.length, l._state.earlier_calls.length, l._state.earlier_calls[0].outcome], [1, 1, "no_contact"], "earlier call kept");
    t += 601_000; await verify(A, "founded_year", "1900", a);
    l = (await ledger(A, a)).body; eq([l.verified, l.wrong, l._state.earlier_calls.length], [{}, 1, 2], "fresh call after idle time");
    const never = makeApp({ ...data, session: { idleResetSeconds: 0, resetAfterCloseSeconds: 0 } });
    await svc("close_call", A, CLOSE, never); t += 86_400_000; eq((await svc("get_account", A, {}, never)).body.ok, false, "0 = never resets");
  } finally { Date.now = realNow; }
});

await scenario("variant data: a date question with a day/month hint, three wrong answers, pass at 1", async () => {
  const data = structuredClone(DATA);
  data.identity = { ...data.identity, maxWrong: 3, passAt: 1, fields: { date_of_birth: { type: "date", swapHint: "day_month_swapped", hints: [] }, city: { type: "text", normalise: { remove: "\\s", case: "fold" }, hints: [] } } };
  data.services.verify_identity.fields.field.enum = ["date_of_birth", "city"];
  data.customers = { X1: { identity: { date_of_birth: "1990-03-04", city: "İzmir" }, products: [], faults: {} } };
  const a = makeApp(data);
  let r = await verify("X1", "date_of_birth", "1990-04-03", a); eq(r.body, { ok: true, verified: false, wrong_answers_left: 3, all_verified: false, hint: "day_month_swapped" }, "swap hint");
  r = await verify("X1", "date_of_birth", "4 March 1990", a); eq(r.body, { ok: false, error: "unreadable date, send it as YYYY-MM-DD" }, "unreadable");
  r = await verify("X1", "date_of_birth", "1990-03-05", a); eq(r.body.wrong_answers_left, 2, "wrong date counted");
  r = await verify("X1", "city", " İZMİR ", a); eq(r.body.verified, true, "case 'fold' ignores Turkish i variants and accents");
  r = await verify("X1", "date_of_birth", "1990-03-04", a); eq(r.body, { ok: true, verified: true, wrong_answers_left: 2, all_verified: true }, "right date, pass at 1");
  r = await svc("get_account", "X1", {}, a); eq(r.body, { ok: true, products: [] }, "account");
});

console.log(failed ? `\n${failed} scenario(s) FAILED` : "\nall scenarios passed");
process.exit(failed ? 1 : 0);
