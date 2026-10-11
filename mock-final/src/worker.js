import table from "./test-customers.json";
import { handle, index } from "./engine.js";

const ix = index(table);
export default { fetch: (request) => handle(request, ix, { bank: "Freya Bank voice test environment (mock)", version: "final-1" }) };
