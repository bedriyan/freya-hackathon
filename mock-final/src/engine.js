// Freya Bank voice test environment, statement and debt services: a mock that serves the bank's listed answers.
// Paths, request fields and bodies follow sources/documents/2026-10-01-service-contract.md; the answers are the ones of
// test-environment/test-customers.json, status code included. Nothing is stored: every request is answered from the table.

const SERVICES = ["cards.list", "cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change", "contact.email.get", "statement.pdf.send"];
const BY_CUSTOMER = new Set(["cards.list", "contact.email.get", "statement.pdf.send"]);
const BY_WEEK = new Set(["statement.week.decide", "statement.week.change"]);
// the service's own 404 from section 4 of the contract; a service without one answers 400 INVALID_ARGUMENTS_ERROR
const NOT_FOUND = {
  "cards.list": ["FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı."],
  "cards.balance": ["FB_IVR_CHANNEL_120", "Kart bakiye servisi hatası: kart bulunamadı"],
  "statement.week.get": ["FB_IVR_CHANNEL_121", "Kart bulunamadı"],
  "statement.pdf.send": ["FB_IVR_CHANNEL_107", "Pdf dokümanı bulunamadı"],
};
const REQUIRED = {
  "cards.list": [["customerNo", "customer number"]],
  "cards.balance": [["cardNumber", "card number"]],
  "statement.week.get": [["cardNumber", "card number"]],
  "statement.week.decide": [["cardNumber", "card number"], ["newStatementWeek", "newStatementWeek"]],
  "statement.week.change": [["cardNumber", "card number"], ["newStatementWeek", "newStatementWeek"]],
  "contact.email.get": [["customerNo", "customer number"], ["intAddressInfoType", "intAddressInfoType"]],
  "statement.pdf.send": [["customerNo", "customer number"]],
};

function guid() { return crypto.randomUUID().replace(/-/g, ""); }

function error(http, code, message, type, rootCause) {
  const g = guid();
  return { http, response: { result: { guid: g, returnCode: code, messageText: message, code, message, type, details: { guid: g, rootCause, errorOrigin: "ivr-gateway-test:ivr-channel-v0" } } } };
}
const invalid = (message) => error(400, "INVALID_ARGUMENTS_ERROR", message, "ERROR", "InvalidArgumentsException");
const missing = (service) => NOT_FOUND[service]
  ? error(404, NOT_FOUND[service][0], NOT_FOUND[service][1], "WARNING", "com.freyabank.ivrgateway.ivrchannel.rest.exception.NotFoundException")
  : invalid("no record for the given arguments");

const text = (v) => (v === undefined || v === null ? "" : String(v).trim());
const isTemplate = (v) => /^\{\{.*\}\}$/.test(text(v));     // the panel's "Test API Request" sends {{variable}} unfilled

export function index(table) {
  const customers = new Map(), cards = {};
  for (const s of SERVICES) cards[s] = new Map();
  for (const c of table.customers) {
    customers.set(text(c.customerNo), c);
    for (const s of SERVICES) if (!BY_CUSTOMER.has(s)) for (const [card, entry] of Object.entries(c.services[s] || {})) cards[s].set(card, entry);
  }
  return { customers, cards, first: table.customers[0] };
}

// -> { http, response, delaySeconds? }
export function answer(ix, service, data) {
  for (const [field, label] of REQUIRED[service]) if (text(data[field]) === "") return invalid(`${label} cannot be empty`);
  if (BY_CUSTOMER.has(service)) {
    const customer = isTemplate(data.customerNo) ? ix.first : ix.customers.get(text(data.customerNo));
    return (customer && customer.services[service]) || missing(service);
  }
  let card = text(data.cardNumber);
  if (isTemplate(card)) card = (ix.first.services["cards.list"].response.data.cards[0] || {}).cardNumber;
  const entry = ix.cards[service].get(card);
  if (!entry) return missing(service);
  if (!BY_WEEK.has(service)) return entry;
  return entry[text(data.newStatementWeek)] || missing(service);
}

const json = (http, body) => new Response(JSON.stringify(body), { status: http, headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" } });

export async function handle(request, ix, info) {
  const url = new URL(request.url);
  const last = url.pathname.replace(/\/+$/, "").split("/").pop();
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "POST, GET, OPTIONS" } });
  if (request.method === "GET" && (last === "health" || last === "")) return json(200, { ok: true, ...info, services: SERVICES.map((s) => "/voice/v1/" + s), customers: ix.customers.size });
  if (last === "token" && request.method === "POST") return json(200, { access_token: "test-token", token_type: "Bearer", expires_in: 1800 });      // the token is not checked in the test environment
  if (!SERVICES.includes(last)) return json(404, { error: "unknown path", path: url.pathname });
  if (request.method !== "POST") return json(405, { error: "every service is POST" });
  let body = null;
  try { body = await request.json(); } catch (e) { body = null; }
  const data = body && typeof body === "object" && body.data && typeof body.data === "object" ? body.data : {};
  const out = answer(ix, last, data);
  if (out.delaySeconds) await new Promise((r) => setTimeout(r, out.delaySeconds * 1000));
  return json(out.http, out.response);
}
