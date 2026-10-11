# Bank mock on Cloudflare Workers

A generic mock of a bank's phone-agent services. Everything about the bank is in **one file, `src/data.js`**; the engine does not change.

    src/data.js        the bank: paths, fields, enums, identity rules, error bodies, customers   <- the only file to fill tonight
    src/engine.js      generic behaviour (validation, identity check, ledger, routing). Mirrors local-simulation/sim/bank.py
    src/worker.js      Worker entry + Durable Object class `BankCustomer` (one object per customer_id, state in its storage)
    tools/make_data.py writes src/data.js from an OpenAPI file + a customers JSON/XLSX
    test/run.mjs       scenario test in Node, no dependencies (fake Durable Objects in test/fake.mjs)
    test/compare.py    plays the same calls against sim/bank.py and the Worker (via test/serve.mjs) and diffs them
    wrangler.toml      name freya-bank-mock, Durable Object binding CUSTOMER, SQLite migration

Routes: `POST /v1/<service>` (JSON body with `customer_id`), `GET /health`, `GET /v1/_ledger/<customer_id>`, `POST /v1/_reset/<customer_id>`.

## Tonight, in order (run everything from `mock/`)

1. Fill the data from the contract and the customer list:

       ../.venv/bin/python tools/make_data.py --api <contract-openapi.json> --customers <customers.json|.xlsx> --version "final-1"

   Read the TODOs it prints, open `src/data.js` and check against the contract: `basePath`, each service's `fields`/enums, `identity.maxWrong`,
   `identity.fields.*.hints`, the `errors` texts and status codes, the customers' ids (the key must be the `customer_id` the agent sends),
   and `session` (see "Decisions" below). If the contract is not an OpenAPI file, copy the generated example and edit by hand.

2. Test locally (no network, no wrangler):

       node test/run.mjs

   The first block of `test/run.mjs` holds the example bank's customers and answers: change those constants for the new bank
   (two customer ids, their right answers, one wrong-shape answer, one promise, one bad enum value).
   Optional, if the customers are in our case format: `../.venv/bin/python test/compare.py --api <api> --cases <cases.json>`

3. Log in (a human, opens the browser):  `npx wrangler login`
4. Deploy:  `npx wrangler deploy`
   The URL is printed: `https://freya-bank-mock.<subdomain>.workers.dev`  (the agent's tools call `<that>/v1/<service>`)
5. Smoke test:

       U=https://freya-bank-mock.<subdomain>.workers.dev
       curl -s $U/health
       curl -s -X POST $U/v1/verify_identity -H 'content-type: application/json' -d '{"customer_id":"S02-01","field":"company_number","answer":"SC418802"}'
       curl -s $U/v1/_ledger/S02-01
       curl -s -X POST $U/v1/_reset/S02-01        # clear the customer you just touched

6. Logs, live:  `npx wrangler tail`   (every request prints method, path, status)
7. After any change to `src/data.js`: `node test/run.mjs && npx wrangler deploy` (state of the customers survives a deploy; reset the ones you used).

## Decisions to take when filling data.js

- `session`: the Python bank never forgets: after `close_call` a customer refuses everything until `_reset`. The example data instead starts
  a fresh call by itself 120 s after `close_call` or after 600 s without any call, so that our own test calls cannot leave a customer
  closed or half-failed when the organisers call. Set both to 0 for the strict behaviour.
- `adminKey`: `_ledger` and `_reset` are open by default (anyone who knows a customer_id can reset it). Set a key to require `x-admin-key`.
- `identity.fields.*.normalise.case`: `"upper"` copies the Python bank; use `"fold"` for names/cities with Turkish letters.
- Enum error text prints the list Python-style (`['a', 'b']`), like bank.py. If the contract shows another text, edit `errors.badEnum`
  (the `{enum}` formatting itself is `pyList` in engine.js).

## Where to cut if the real contract is different

- **A service that is not one of the seven**: add it under `services` in data.js. If it only has to accept and record, `kind: "record"` with
  its own `list` is enough (no code). New behaviour = one new function in `KINDS` in `src/engine.js` (about 10 lines) plus its `kind` name.
- **Different success body**: `services.<name>.response` in data.js. A new computed value needs a `$name` added in the matching `KINDS` function.
- **Different checks or their order** (types, formats, rules between fields): `validate()` and `run()` in engine.js. Today: closed, fault,
  missing required, then per field enum / number / date, then `requiresVerified`. Booleans and plain strings are not type-checked.
- **Identity rules** other than "N wrong in total ends it, hints not counted" (per-question limits, lock-out answers, extra response fields):
  `KINDS.verify` in engine.js.
- **Session handling other than "state per customer_id"** (a session-start route that hands out ids, a session id header, a key header):
  `handleRequest()` in engine.js picks the Durable Object in one place (`toCustomer(..., customerId)`): take the key from the header/path
  there, and add the session-start route next to the `_ledger`/`_reset` block. If ids are created at run time, `handleCustomer()` must look
  the customer record up from what the session stored instead of `data.customers[msg.customerId]`.
- **Authentication header on the services**: one check at the top of `handleRequest()`.
- **Another ledger shape**: `ledger()` in engine.js.
