// node test/run.mjs [base url] [--skip-slow]   Without a url the engine is called in this process; with one, the deployed Worker.
// Sends every request the test customer file has an answer for and compares status code and body exactly; then the not-found and bad-request cases.
import fs from "node:fs";
import { handle, index } from "../src/engine.js";
const table = JSON.parse(fs.readFileSync(new URL("../src/test-customers.json", import.meta.url)));
const base = process.argv.slice(2).find((x) => x.startsWith("http")), ix = index(table), skipSlow = process.argv.includes("--skip-slow");
async function call(service, data, raw) {
  const init = { method: "POST", headers: { "content-type": "application/json" }, body: raw ?? JSON.stringify({ data, call: { ani: "05000000000", lang: "tr" } }) };
  const r = base ? await fetch(base.replace(/\/+$/, "") + "/voice/v1/" + service, init) : await handle(new Request("https://mock.test/voice/v1/" + service, init), ix, {});
  return { http: r.status, body: await r.json() };
}
let n = 0, bad = 0;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
async function expectEntry(service, data, entry, label) {
  if (entry.delaySeconds && skipSlow) return;
  const t = Date.now(), got = await call(service, data); n++;
  if (got.http !== entry.http || !same(got.body, entry.response)) { bad++; console.log("DIFF", label, got.http, JSON.stringify(got.body).slice(0, 200)); }
  if (entry.delaySeconds && Date.now() - t < entry.delaySeconds * 1000) { bad++; console.log("NO DELAY", label); }
}
async function expectCode(service, data, http, code, label, raw) {
  const got = await call(service, data, raw); n++;
  const r = got.body.result || {};
  if (got.http !== http || r.code !== code || r.returnCode !== code || "data" in got.body || r.message !== r.messageText) { bad++; console.log("DIFF", label, got.http, JSON.stringify(got.body).slice(0, 200)); }
}
const pdf = (v) => ({ customerNo: v, cardNumber: "x", currencyType: "TL", statementDate: "05.10.2026", emailAddress: "a@b.example" });
for (const c of table.customers) {
  const s = c.services, no = c.customerNo;
  for (const v of [no, Number(no)]) {
    if (s["cards.list"]) await expectEntry("cards.list", { customerNo: v, aiFlow: "CardOps" }, s["cards.list"], `cards.list ${no}`);
    if (s["contact.email.get"]) for (const t of ["E", "P", "B"]) await expectEntry("contact.email.get", { customerNo: v, intAddressInfoType: t }, s["contact.email.get"], `contact.email.get ${no}`);
    if (s["statement.pdf.send"]) await expectEntry("statement.pdf.send", pdf(v), s["statement.pdf.send"], `statement.pdf.send ${no}`);
    else await expectCode("statement.pdf.send", pdf(v), 404, "FB_IVR_CHANNEL_107", `statement.pdf.send ${no} (no entry)`);
  }
  for (const service of ["cards.balance", "statement.week.get"]) for (const [card, e] of Object.entries(s[service] || {})) await expectEntry(service, { cardNumber: card }, e, `${service} ${card}`);
  for (const service of ["statement.week.decide", "statement.week.change"]) for (const [card, weeks] of Object.entries(s[service] || {})) {
    for (const [w, e] of Object.entries(weeks)) for (const v of [w, Number(w)]) await expectEntry(service, { cardNumber: card, newStatementWeek: v, statementDay: 5 }, e, `${service} ${card} week ${w}`);
    for (const w of ["1", "2", "3", "4"]) if (!weeks[w]) await expectCode(service, { cardNumber: card, newStatementWeek: w, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR", `${service} ${card} current week ${w}`);
  }
}
await expectCode("cards.list", { customerNo: "11111111" }, 404, "FB_IVR_CHANNEL_102", "unknown customer cards.list");
await expectCode("cards.balance", { cardNumber: "999999******0000" }, 404, "FB_IVR_CHANNEL_120", "unknown card balance");
await expectCode("statement.week.get", { cardNumber: "999999******0000" }, 404, "FB_IVR_CHANNEL_121", "unknown card week.get");
await expectCode("statement.week.decide", { cardNumber: "999999******0000", newStatementWeek: 2 }, 400, "INVALID_ARGUMENTS_ERROR", "unknown card decide");
await expectCode("statement.week.change", { cardNumber: "999999******0000", newStatementWeek: 2 }, 400, "INVALID_ARGUMENTS_ERROR", "unknown card change");
await expectCode("contact.email.get", { customerNo: "11111111", intAddressInfoType: "E" }, 400, "INVALID_ARGUMENTS_ERROR", "unknown customer email");
await expectCode("statement.pdf.send", { customerNo: "11111111" }, 404, "FB_IVR_CHANNEL_107", "unknown customer pdf");
await expectCode("cards.balance", { cardNumber: "" }, 400, "INVALID_ARGUMENTS_ERROR", "empty card number");
await expectCode("cards.balance", {}, 400, "INVALID_ARGUMENTS_ERROR", "no card number");
await expectCode("contact.email.get", { customerNo: "90000001", intAddressInfoType: "" }, 400, "INVALID_ARGUMENTS_ERROR", "empty intAddressInfoType");
await expectCode("cards.list", {}, 400, "INVALID_ARGUMENTS_ERROR", "not json", "not json");
console.log(`${n} requests, ${bad} different`);
process.exit(bad ? 1 : 0);
