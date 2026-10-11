// THE ONE FILE ABOUT THE BANK. The engine (engine.js, worker.js) reads everything bank-specific from here.
// Written by tools/make_data.py from: ../local-simulation/blind/s02/openapi.json + ../local-simulation/blind/s02/cases.json
// To refill for another bank: run make_data.py on its contract + customer list, then go through the TODOs and the comments below.
// Open decisions left by the generator:
//   (none)

export default {
  // Shown by GET /health. Change it on every deploy so you can see which data is live.
  version: "Calder & Forth Bank, Business Banking: o / 2026-10-11",

  // Prefix of every service path (the last part of the contract's server URL). Service URL = <worker url> + basePath + service.path
  basePath: "/v1",

  // The body field that carries the customer's key. Every call is routed to the Durable Object of this value.
  customerIdField: "customer_id",

  // Name of the Durable Object binding in wrangler.toml.
  binding: "CUSTOMER",

  // If set, GET <basePath>/_ledger/<id> and POST <basePath>/_reset/<id> need the header x-admin-key with this value. "" = open.
  adminKey: "",

  // When does a customer's state start again by itself? (POST <basePath>/_reset/<customer_id> always clears it.)
  //   idleResetSeconds        a service call that arrives this long after the previous one starts a fresh call (0 = never)
  //   resetAfterCloseSeconds  a service call that arrives this long after close_call starts a fresh call (0 = never: stays closed)
  // The finished call is kept under _state.earlier_calls of the ledger (last 3). The Python bank never resets: set both to 0 to copy it.
  session: { idleResetSeconds: 600, resetAfterCloseSeconds: 120 },

  // The key inside a product record that create_promise / add_note refer to.
  productIdKey: "product_id",

  // ---- Services. One entry per POST path of the contract.
  //   path              appended to basePath
  //   kind              what the engine does:
  //                       "verify"  checks one identity answer (rules under `identity` below)
  //                       "account" returns the customer's products
  //                       "promise" records one entry per product in `list`, answers with a new id (idPrefix + running number)
  //                       "record"  appends the call's fields to the ledger list `list` (notes, callbacks, transfers, anything else)
  //                       "close"   keeps the fields named in `store` in the ledger and refuses every later call (errors.closed)
  //   fields            request fields besides the customer id: required, type (string | number | integer | boolean | date), enum.
  //                     Checked in this order: missing required field, then per field enum / number / date. Errors: see `errors`.
  //   requiresVerified  true = refused with errors.notVerified until the identity check is passed
  //   response          the success body. "$name" is replaced by a value of the engine:
  //                       verify: $verified $wrong_left $all_verified (+ $hint in hintResponse) / account: $products / promise: $id
  //   promise only      productRef (request field holding the product id), idKey, idPrefix, copyFromProduct (product keys copied
  //                     into the ledger entry), onePerProduct (a second promise for the same product -> errors.duplicatePromise)
  //   record only       productRef (optional: if set, an unknown product id is refused)
  //   stringify         fields stored as text in the ledger
  services: {
    "verify_identity": {
      "path": "/verify_identity",
      "kind": "verify",
      "summary": "Check one security answer. Two wrong answers in total end the check.",
      "fields": {
        "field": {"required": true, "type": "string", "enum": ["company_number", "founded_year"]},
        "answer": {"required": true, "type": "string"},
      },
      "response": {"ok": true, "verified": "$verified", "wrong_answers_left": "$wrong_left", "all_verified": "$all_verified"},
      "hintResponse": {"ok": true, "verified": "$verified", "wrong_answers_left": "$wrong_left", "all_verified": "$all_verified", "hint": "$hint"},
    },
    "get_account": {
      "path": "/get_account",
      "kind": "account",
      "summary": "The overdue facilities of the company, one to three. Refused until both security answers are passed.",
      "fields": {},
      "requiresVerified": true,
      "response": {"ok": true, "products": "$products"},
    },
    "create_promise": {
      "path": "/create_promise",
      "kind": "promise",
      "summary": "Book a payment commitment. One per facility.",
      "fields": {
        "product_id": {"required": true, "type": "string"},
        "amount": {"required": true, "type": "number"},
        "date": {"required": true, "type": "date"},
        "part_payment": {"required": true, "type": "boolean"},
      },
      "requiresVerified": true,
      "list": "promises",
      "productRef": "product_id",
      "idKey": "promise_id",
      "idPrefix": "pr",
      "copyFromProduct": ["type"],
      "onePerProduct": true,
      "stringify": ["date"],
      "response": {"ok": true, "promise_id": "$id"},
    },
    "add_note": {
      "path": "/add_note",
      "kind": "record",
      "summary": "Record why a facility ended without a commitment.",
      "fields": {
        "product_id": {"required": true, "type": "string"},
        "reason": {"required": true, "type": "string", "enum": ["declined", "disputed", "payment_claimed", "financial_difficulty", "system_error", "site_visit"]},
        "payment_date": {"required": false, "type": "date"},
        "text": {"required": false, "type": "string"},
      },
      "requiresVerified": false,
      "list": "notes",
      "stringify": ["payment_date"],
      "response": {"ok": true},
    },
    "schedule_callback": {
      "path": "/schedule_callback",
      "kind": "record",
      "summary": "Book a callback to the named contact.",
      "fields": {
        "date": {"required": true, "type": "date"},
        "time": {"required": true, "type": "string"},
        "arranged_with": {"required": true, "type": "string", "enum": ["named_contact", "third_party"]},
      },
      "requiresVerified": false,
      "list": "callbacks",
      "stringify": ["date", "time"],
      "response": {"ok": true},
    },
    "transfer_to_agent": {
      "path": "/transfer_to_agent",
      "kind": "record",
      "summary": "Hand the call over to a team.",
      "fields": {
        "queue": {"required": true, "type": "string", "enum": ["insolvency_team", "payment_tracing", "business_support", "sme_servicing", "fraud_ops"]},
      },
      "requiresVerified": false,
      "list": "transfers",
      "stringify": [],
      "response": {"ok": true},
    },
    "close_call": {
      "path": "/close_call",
      "kind": "close",
      "summary": "End the call. Always the last tool call. Nothing is accepted after it.",
      "fields": {
        "outcome": {"required": true, "type": "string", "enum": ["commitment", "payment_claimed", "handed_over", "no_commitment", "identity_failed", "rearranged", "no_contact"]},
        "reason": {"required": false, "type": "string", "enum": ["insolvency", "payment_trace", "financial_difficulty", "person_requested", "facility_review", "disputed", "declined", "system_error", "wrong_answers", "would_not_answer", "time_agreed", "no_time_agreed", "callback_arranged", "no_suitable_time", "wrong_number", "left_company", "not_confirmed"]},
        "identity_verified": {"required": true, "type": "boolean"},
      },
      "store": ["outcome", "reason"],
      "response": {"ok": true},
    },
  },

  // ---- Identity check (the "verify" service).
  //   fieldParam / answerParam  request fields: which question, and the answer given
  //   maxWrong   wrong answers in total (over all questions) that end the check. After that every verify call gets errors.identityEnded
  //              and the customer can never be verified in this call. Hints and unreadable dates are not counted.
  //   passAt     how many questions must be passed. null = all of the customer's identity fields.
  //   fields.<question>:
  //     type       "text": compared after `normalise` ({remove: regex of characters to drop, case: "upper" | "lower" | "fold" | null};
  //                        "fold" also ignores accents and the Turkish dotted/dotless i),
  //                        so "sc 418-802" equals "SC418802".
  //                "date": answer must be YYYY-MM-DD (else errors.unreadableDate, not counted); swapHint = hint name returned (not
  //                        counted) when day and month are exchanged, or null.
  //     hints      [{pattern, hint}]: an answer that matches the whole regex gets `hint` back and is NOT counted as wrong.
  //                Tested before the answer is compared, so the pattern must not match a complete right answer.
  identity: {
    "fieldParam": "field",
    "answerParam": "answer",
    "maxWrong": 2,
    "passAt": null,
    "fields": {
      "company_number": {
        "type": "text",
        "normalise": {"remove": "[\\s-]", "case": "upper"},
        "hints": [
          {"hint": "incomplete_number", "pattern": "[A-Za-z0-9 -]{1,7}"},
        ],
      },
      "founded_year": {
        "type": "text",
        "normalise": {"remove": "[\\s-]", "case": "upper"},
        "hints": [],
      },
    },
  },

  // ---- Error answers: status code and exact body. {field}, {service}, {enum} are filled in by the engine.
  // Copy the contract's texts here if it gives any.
  errors: {
    notFound:             { status: 404, body: { ok: false, error: "not found" } },
    badBody:              { status: 400, body: { ok: false, error: "the body must be a JSON object" } },
    unknownCustomer:      { status: 404, body: { ok: false, error: "unknown customer_id" } },
    unknownService:       { status: 404, body: { ok: false, error: "unknown tool {service}" } },
    closed:               { status: 200, body: { ok: false, error: "the call is closed, nothing is accepted after close_call" } },
    fault:                { status: 500, body: { ok: false, error: "service unavailable, nothing was recorded" } },
    missingField:         { status: 400, body: { ok: false, error: "missing field {field}" } },
    badEnum:              { status: 400, body: { ok: false, error: "{field} must be one of {enum}" } },
    badNumber:            { status: 400, body: { ok: false, error: "{field} must be a number" } },
    badDate:              { status: 400, body: { ok: false, error: "{field} must be a date as YYYY-MM-DD" } },
    notVerified:          { status: 200, body: { ok: false, error: "identity not verified" } },
    unknownIdentityField: { status: 400, body: { ok: false, error: "unknown field {field}" } },
    identityEnded:        { status: 200, body: { ok: false, error: "the identity check has ended after too many wrong answers" } },
    unreadableDate:       { status: 200, body: { ok: false, error: "unreadable date, send it as YYYY-MM-DD" } },
    unknownProduct:       { status: 200, body: { ok: false, error: "unknown product_id" } },
    duplicatePromise:     { status: 200, body: { ok: false, error: "this product already has a promise" } },
  },

  // ---- Customers, keyed by the customer id the agent sends.
  //   identity     right answer per question (text; dates as YYYY-MM-DD)
  //   products     what the "account" service returns, in order; each needs the productIdKey
  //   faults       {service: N} = the first N calls of that service answer errors.fault (500) and record nothing. {} = none.
  //   ledgerExtra  copied into the ledger as is (for our scorer)
  //   variables    not used by the engine; the call variables the agent gets for this customer, kept here for reference
  customers: {
    "S02-01": {
      "identity": {"company_number": "SC418802", "founded_year": "2012"},
      "products": [
        {"product_id": "p1", "type": "business_loan", "reference_last3": "417", "overdue_amount": 1840.0, "days_overdue": 9, "monthly_instalment": 920.0},
      ],
      "faults": {},
      "ledgerExtra": {"case": "S02-01", "now": "2026-12-22T09:50"},
      "variables": {"first_name": "Fiona", "last_name": "Baxter", "company_name": "Baxter & Lyle Joinery Ltd", "now": "2026-12-22T09:50"},
    },
    "S02-02": {
      "identity": {"company_number": "09317745", "founded_year": "2014"},
      "products": [
        {"product_id": "p1", "type": "business_overdraft", "reference_last3": "062", "overdue_amount": 2650.0, "days_overdue": 52, "agreed_limit": 15000.0},
      ],
      "faults": {},
      "ledgerExtra": {"case": "S02-02", "now": "2026-11-12T15:40"},
      "variables": {"first_name": "Gareth", "last_name": "Pryce", "company_name": "Pryce Haulage Ltd", "now": "2026-11-12T15:40"},
    },
    "S02-03": {
      "identity": {"company_number": "07741903", "founded_year": "2009"},
      "products": [
        {"product_id": "p1", "type": "equipment_lease", "reference_last3": "338", "overdue_amount": 1260.0, "days_overdue": 11, "monthly_rental": 630.0},
      ],
      "faults": {},
      "ledgerExtra": {"case": "S02-03", "now": "2026-11-09T10:30"},
      "variables": {"first_name": "Priya", "last_name": "Raman", "company_name": "Raman Dental Supplies Ltd", "now": "2026-11-09T10:30"},
    },
    "S02-04": {
      "identity": {"company_number": "08256610", "founded_year": "2013"},
      "products": [
        {"product_id": "p1", "type": "business_overdraft", "reference_last3": "905", "overdue_amount": 3185.5, "days_overdue": 20, "agreed_limit": 10000.0},
      ],
      "faults": {},
      "ledgerExtra": {"case": "S02-04", "now": "2026-11-09T10:30"},
      "variables": {"first_name": "Tomasz", "last_name": "Nowak", "company_name": "Nowak Print & Sign Ltd", "now": "2026-11-09T10:30"},
    },
    "S02-05": {
      "identity": {"company_number": "06892214", "founded_year": "2008"},
      "products": [
        {"product_id": "p1", "type": "equipment_lease", "reference_last3": "551", "overdue_amount": 1470.0, "days_overdue": 9, "monthly_rental": 490.0},
        {"product_id": "p2", "type": "business_loan", "reference_last3": "129", "overdue_amount": 2300.0, "days_overdue": 30, "monthly_instalment": 1150.0},
      ],
      "faults": {"create_promise": 1},
      "ledgerExtra": {"case": "S02-05", "now": "2026-11-20T11:15"},
      "variables": {"first_name": "Helen", "last_name": "Ashworth", "company_name": "Ashworth Catering Ltd", "now": "2026-11-20T11:15"},
    },
    "S02-06": {
      "identity": {"company_number": "SC302957", "founded_year": "1998"},
      "products": [
        {"product_id": "p1", "type": "business_loan", "reference_last3": "774", "overdue_amount": 1980.0, "days_overdue": 10, "monthly_instalment": 990.0},
        {"product_id": "p2", "type": "business_overdraft", "reference_last3": "206", "overdue_amount": 4120.0, "days_overdue": 25, "agreed_limit": 20000.0},
      ],
      "faults": {},
      "ledgerExtra": {"case": "S02-06", "now": "2026-11-25T14:10"},
      "variables": {"first_name": "Duncan", "last_name": "Reid", "company_name": "Reid Marine Services Ltd", "now": "2026-11-25T14:10"},
    },
  },
};
