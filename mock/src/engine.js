// Generic bank-mock engine. Everything that depends on a particular bank comes in as `data` (src/data.js).
// Nothing here names an identity question, an enum value or a product field.
//
//   handleRequest(request, env, data)   the Worker side: routing, CORS, health, sends each call to the customer's Durable Object
//   handleCustomer(storage, msg, data)  the Durable Object side: one customer's state, the services, the ledger
//
// Behaviour mirrors local-simulation/sim/bank.py (order of checks, error answers, ledger shape).

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,OPTIONS",
  "access-control-allow-headers": "*",
  "access-control-max-age": "86400",
};

export function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "content-type": "application/json; charset=utf-8" } });
}

const has = (o, k) => o != null && Object.prototype.hasOwnProperty.call(o, k);

// "$name" as a whole string -> the raw value ctx[name]; "{name}" inside a string -> its text.
function fill(t, ctx) {
  if (typeof t === "string") {
    if (t[0] === "$" && has(ctx, t.slice(1))) return ctx[t.slice(1)];
    return t.replace(/\{(\w+)\}/g, (m, k) => (has(ctx, k) ? String(ctx[k]) : m));
  }
  if (Array.isArray(t)) return t.map((x) => fill(x, ctx));
  if (t && typeof t === "object") return Object.fromEntries(Object.entries(t).map(([k, v]) => [k, fill(v, ctx)]));
  return t;
}
const err = (e, ctx = {}) => [e.status, fill(e.body, ctx)];
const pyList = (xs) => "[" + xs.map((x) => (typeof x === "string" ? `'${x}'` : String(x))).join(", ") + "]";

function parseDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s));
  if (!m) return null;
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (y < 1 || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return { y, m: mo, d };
}

function toNumber(v, integer) {
  let n;
  if (typeof v === "number") n = v;
  else if (typeof v === "boolean") n = v ? 1 : 0;
  else if (typeof v === "string" && /^\s*[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?\s*$/i.test(v)) n = Number(v);
  else return undefined;
  if (!Number.isFinite(n)) return undefined;
  return integer ? Math.trunc(n) : n;
}

function normalise(s, rule = {}) {
  let out = String(s);
  if (rule.remove) out = out.replace(new RegExp(rule.remove, "g"), "");
  if (rule.case === "upper") out = out.toUpperCase();
  if (rule.case === "lower") out = out.toLowerCase();
  // "fold": no case, no accents, Turkish dotted/dotless i treated alike ("İZMİR" = "izmir" = "Izmır")
  if (rule.case === "fold") out = out.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/\u0131/g, "i");
  return out;
}

// ---------------------------------------------------------------- state of one customer
const fresh = () => ({ verified: {}, wrong: 0, hinted: false, closed: false, lists: {}, close: {}, failures: {}, calls: [], lastAt: 0, closedAt: 0 });

function allVerified(data, cust, st) {
  const fields = Object.keys(cust.identity || {});
  const passAt = data.identity.passAt || fields.length;
  return fields.length > 0 && fields.filter((f) => st.verified[f]).length >= passAt;
}

function ledger(data, cust, st, extra) {
  const lists = {};
  for (const svc of Object.values(data.services)) if (svc.list) lists[svc.list] = st.lists[svc.list] || [];
  const closeFields = {};
  for (const svc of Object.values(data.services)) for (const f of svc.store || []) closeFields[f] = st.close[f] ?? null;
  return {
    ok: true, ...(cust.ledgerExtra || {}), verified: st.verified, wrong: st.wrong, hinted: st.hinted, ...lists, ...closeFields,
    failures: st.failures, calls: st.calls,
    _state: { closed: st.closed, all_verified: allVerified(data, cust, st), last_at: st.lastAt, ...extra },
  };
}

// ---------------------------------------------------------------- the services
function validate(data, svc, a) {
  const E = data.errors, fields = Object.entries(svc.fields || {});
  for (const [f, spec] of fields) if (spec.required && (a[f] === undefined || a[f] === null || a[f] === "")) return err(E.missingField, { field: f });
  for (const [f, spec] of fields) {
    if (!has(a, f)) continue;
    if (spec.enum && spec.enum.length && !spec.enum.includes(a[f])) return err(E.badEnum, { field: f, enum: pyList(spec.enum) });
    if (spec.type === "number" || spec.type === "integer") {
      const n = toNumber(a[f], spec.type === "integer");
      if (n === undefined) return err(E.badNumber, { field: f });
      a[f] = n;
    }
    if (spec.type === "date" && !parseDate(a[f])) return err(E.badDate, { field: f });
  }
  return null;
}

const KINDS = {
  verify(data, cust, st, svc, a) {
    const I = data.identity, E = data.errors;
    const field = a[I.fieldParam], answer = String(a[I.answerParam]).trim();
    if (typeof field !== "string" || !has(cust.identity, field)) return err(E.unknownIdentityField, { field });
    if (st.wrong >= I.maxWrong) return err(E.identityEnded);
    const want = String(cust.identity[field]).trim(), rule = (I.fields || {})[field] || {};
    const ctx = () => ({ verified: false, wrong_left: I.maxWrong - st.wrong, all_verified: allVerified(data, cust, st) });
    for (const h of rule.hints || []) {
      if (new RegExp("^(?:" + h.pattern + ")$").test(answer)) { st.hinted = true; return [200, fill(svc.hintResponse, { ...ctx(), hint: h.hint })]; }
    }
    let right;
    if (rule.type === "date") {
      const got = parseDate(answer), exp = parseDate(want);
      if (!got) return err(E.unreadableDate, { field });
      right = !!exp && got.y === exp.y && got.m === exp.m && got.d === exp.d;
      if (rule.swapHint && exp && !right && got.y === exp.y && got.m === exp.d && got.d === exp.m) {
        st.hinted = true; return [200, fill(svc.hintResponse, { ...ctx(), hint: rule.swapHint })];
      }
    } else right = normalise(answer, rule.normalise) === normalise(want, rule.normalise);
    if (right) st.verified[field] = true; else st.wrong += 1;
    return [200, fill(svc.response, { ...ctx(), verified: right })];
  },

  account(data, cust, st, svc) {
    return [200, fill(svc.response, { products: cust.products || [] })];
  },

  promise(data, cust, st, svc, a) {
    const E = data.errors, list = (st.lists[svc.list] ||= []), ref = a[svc.productRef];
    const prod = (cust.products || []).find((p) => p[data.productIdKey] === ref);
    if (!prod) return err(E.unknownProduct);
    if (svc.onePerProduct && list.some((p) => p[svc.productRef] === ref)) return err(E.duplicatePromise);
    const id = svc.idPrefix + (list.length + 1), rec = { [svc.idKey]: id };
    for (const k of svc.copyFromProduct || []) rec[k] = prod[k] ?? null;
    Object.assign(rec, a);
    for (const k of svc.stringify || []) if (has(rec, k)) rec[k] = String(rec[k]);
    list.push(rec);
    return [200, fill(svc.response, { id })];
  },

  record(data, cust, st, svc, a) {
    if (svc.productRef && !(cust.products || []).some((p) => p[data.productIdKey] === a[svc.productRef])) return err(data.errors.unknownProduct);
    const rec = { ...a };
    for (const k of svc.stringify || []) if (has(rec, k)) rec[k] = String(rec[k]);
    (st.lists[svc.list] ||= []).push(rec);
    return [200, fill(svc.response, {})];
  },

  close(data, cust, st, svc, a) {
    for (const f of svc.store || []) st.close[f] = a[f] || null;
    st.closed = true; st.closedAt = Date.now();
    return [200, fill(svc.response, {})];
  },
};

function run(data, cust, st, name, a) {
  const svc = data.services[name], E = data.errors;
  if (st.closed) return err(E.closed);
  if ((st.failures[name] || 0) < ((cust.faults || {})[name] || 0)) { st.failures[name] = (st.failures[name] || 0) + 1; return err(E.fault); }
  const bad = validate(data, svc, a);
  if (bad) return bad;
  if (svc.requiresVerified && !allVerified(data, cust, st)) return err(E.notVerified);
  return KINDS[svc.kind](data, cust, st, svc, a);
}

// ---------------------------------------------------------------- Durable Object side
// msg = {op: "call", customerId, service, args} | {op: "ledger", customerId} | {op: "reset", customerId}
export async function handleCustomer(storage, msg, data) {
  const cust = data.customers[msg.customerId];
  if (!cust) return json(data.errors.unknownCustomer.status, data.errors.unknownCustomer.body);
  let st = (await storage.get("state")) || fresh();
  const history = (await storage.get("history")) || [];
  if (msg.op === "ledger") return json(200, ledger(data, cust, st, { earlier_calls: history }));
  if (msg.op === "reset") { await storage.delete("state"); await storage.delete("history"); return json(200, { ok: true, reset: msg.customerId }); }

  // optional: start a new call by itself when the previous one is over (see data.session)
  const S = data.session || {}, now = Date.now();
  const idle = S.idleResetSeconds && st.lastAt && now - st.lastAt > S.idleResetSeconds * 1000;
  const done = S.resetAfterCloseSeconds && st.closed && now - st.closedAt > S.resetAfterCloseSeconds * 1000;
  if (idle || done) {
    history.push(ledger(data, cust, st, { ended_by: idle ? "idle" : "closed" }));
    await storage.put("history", history.slice(-3));
    st = fresh();
  }
  const args = { ...msg.args };
  const [status, result] = run(data, cust, st, msg.service, args);
  st.calls.push({ n: st.calls.length + 1, tool: msg.service, args, status, result });
  st.lastAt = now;
  await storage.put("state", st);
  return json(status, result);
}

// ---------------------------------------------------------------- Worker side
async function toCustomer(env, data, msg) {
  if (typeof msg.customerId !== "string" || !has(data.customers, msg.customerId)) return json(data.errors.unknownCustomer.status, data.errors.unknownCustomer.body);
  const ns = env[data.binding || "CUSTOMER"];
  const stub = ns.get(ns.idFromName(msg.customerId));
  const r = await stub.fetch(new Request("https://customer.internal/", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(msg) }));
  return new Response(r.body, { status: r.status, headers: { ...CORS, "content-type": "application/json; charset=utf-8" } });
}

export async function handleRequest(request, env, data) {
  const E = data.errors, notFound = () => json(E.notFound.status, E.notFound.body);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  const path = new URL(request.url).pathname.replace(/\/+$/, "");
  const base = (data.basePath || "").replace(/\/+$/, "");

  if (request.method === "GET" && (path === "" || path === "/health")) return json(200, { ok: true, customers: Object.keys(data.customers).length, version: data.version });
  if (!path.startsWith(base + "/")) return notFound();
  const rest = path.slice(base.length);                       // "/verify_identity", "/_ledger/<id>", ...

  const admin = /^\/_(ledger|reset)\/(.+)$/.exec(rest);
  if (admin) {
    if (data.adminKey && request.headers.get("x-admin-key") !== data.adminKey) return json(401, { ok: false, error: "wrong or missing x-admin-key" });
    const [, op, id] = admin;
    if ((op === "ledger" && request.method !== "GET") || (op === "reset" && request.method !== "POST")) return notFound();
    return toCustomer(env, data, { op, customerId: decodeURIComponent(id) });
  }

  if (request.method !== "POST") return notFound();
  let body;
  try { const text = await request.text(); body = text ? JSON.parse(text) : {}; } catch { body = null; }
  if (body === null || typeof body !== "object" || Array.isArray(body)) return json(E.badBody.status, E.badBody.body);
  const idField = data.customerIdField;
  if (typeof body[idField] !== "string" || !has(data.customers, body[idField])) return json(E.unknownCustomer.status, E.unknownCustomer.body);
  const service = Object.keys(data.services).find((n) => data.services[n].path.replace(/\/+$/, "") === rest);
  if (!service) return json(E.unknownService.status, fill(E.unknownService.body, { service: rest.split("/").pop() }));
  const { [idField]: customerId, ...args } = body;
  return toCustomer(env, data, { op: "call", customerId, service, args });
}
