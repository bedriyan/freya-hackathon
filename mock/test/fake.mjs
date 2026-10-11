// A minimal in-memory stand-in for the Durable Object runtime, for Node tests. No dependencies.
// Every request builds a NEW object instance on the same storage, so the tests prove the state lives in storage, not in memory.
import worker, { BankCustomer } from "../src/worker.js";
import { handleRequest, handleCustomer } from "../src/engine.js";

class FakeStorage {
  constructor() { this.map = new Map(); }
  async get(k) { return this.map.has(k) ? structuredClone(this.map.get(k)) : undefined; }
  async put(k, v) { this.map.set(k, structuredClone(v)); }
  async delete(k) { return this.map.delete(k); }
}

// data = undefined: the real worker with src/data.js. data given: the same engine with that data (for variants).
export function makeApp(data) {
  const storages = new Map();
  const namespace = {
    idFromName: (name) => ({ name, toString: () => "id:" + name }),
    get(id) {
      if (!storages.has(id.name)) storages.set(id.name, new FakeStorage());
      const ctx = { storage: storages.get(id.name), id };
      return {
        fetch: async (request) => data
          ? handleCustomer(ctx.storage, await request.json(), data)
          : new BankCustomer(ctx, env).fetch(request),
      };
    },
  };
  const env = { [(data && data.binding) || "CUSTOMER"]: namespace };
  const fetch = (request) => (data ? handleRequest(request, env, data) : worker.fetch(request, env, {}));
  return { fetch, env, storages };
}
