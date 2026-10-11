// Cloudflare Worker entry: the wiring only. The generic behaviour is in ./engine.js, everything about the bank in ./data.js.
import DATA from "./data.js";
import { handleRequest, handleCustomer, json } from "./engine.js";

// One instance per customer_id (idFromName). SQLite-backed key-value storage keeps the customer's state strongly consistent.
export class BankCustomer {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; }
  async fetch(request) {
    try { return await handleCustomer(this.ctx.storage, await request.json(), DATA); }
    catch (e) { console.log("customer error", String(e && e.stack || e)); return json(500, { ok: false, error: "service unavailable, nothing was recorded" }); }
  }
}

export default {
  async fetch(request, env, ctx) {
    try {
      const response = await handleRequest(request, env, DATA);
      console.log(request.method, new URL(request.url).pathname, response.status);
      return response;
    } catch (e) {
      console.log("worker error", String(e && e.stack || e));
      return json(500, { ok: false, error: "service unavailable, nothing was recorded" });
    }
  },
};
