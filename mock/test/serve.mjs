// node test/serve.mjs [port] [data.js]   -> the Worker on http://127.0.0.1:<port> with the in-memory fake Durable Objects
// port 0 = any free one. A second argument runs the same engine with another data file instead of src/data.js.
import http from "node:http";
import { makeApp } from "./fake.mjs";

import { pathToFileURL } from "node:url";
const app = makeApp(process.argv[3] ? (await import(pathToFileURL(process.argv[3]).href)).default : undefined);
const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const request = new Request("http://127.0.0.1" + req.url, { method: req.method, headers: req.headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : body });
  const r = await app.fetch(request);
  const out = Buffer.from(await r.arrayBuffer());
  res.writeHead(r.status, { ...Object.fromEntries(r.headers), "content-length": out.length });
  res.end(out);
});
server.listen(Number(process.argv[2] || 0), "127.0.0.1", () => console.log("LISTENING " + server.address().port));
