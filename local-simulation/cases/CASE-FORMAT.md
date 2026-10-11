# How to write test customers for a brief

You are given a bank's brief documents and its tool API file. You write test customers that try to break a phone agent built for that brief. You have not seen the agent and you must not look at it, nor at any existing test file: work from the brief alone.

Output: one JSON file, a list of cases. 24 to 28 cases, spread like this (a program counts it and sends you back for what is missing):

- every outcome and every reason the closing call allows, every note reason and every transfer queue of the tool API file is the expected result of at least one case, unless the brief never leads to it
- at least 6 customers with one product, 5 with two, 5 with three; in at least 3 of them one product gets a promise and another ends without one
- at least 5 calls that end before any product is dealt with (wrong person, identity not passed, a callback), at least 8 that end with a promise
- at least 4 different call days in `now`; at least 3 cases with a tool that fails, on different tools
- at least 8 cases with a way of talking (`style`), each of `pieces`, `corrects`, `talkative`, `asks_back`, `in_words` at least once
 Every value in a case (names, dates, amounts, outcomes, reasons, tool fields) must follow from the brief and the tool API file.

## What makes a good case

Ordinary customers pass with any reasonable agent and tell you nothing. Write customers that sit where rules meet, count or have an exception:

- a rule with a count or limit: reach the limit exactly (the last allowed wrong answer, the last allowed attempt, an offer that may be made once and is refused, a question that may be asked once and gets a non-answer)
- an exception inside a rule ("unless", "if the customer still ...", "this rule comes before ...")
- a boundary value: the last allowed date and the day after it, an amount exactly at a minimum and one just below, a non-working day or holiday, the last moment of an allowed time window
- a relative date that the brief defines ("next <weekday>", "end of the week") on a `now` where it matters; use a different `now` in a few cases (another weekday, a day before a holiday)
- someone else on the line: wrong number, a death, a relative who pushes for information or offers to pay, a callback time that is not allowed
- identity: wrong answers spread over the questions, a refusal, a question back instead of an answer, a date said in an ambiguous order
- pressure: a request the agent may not grant, repeated three times; a false claim ("your colleague promised me ..."); a request for a person
- a change of mind at the read-back, and after the record is made
- a tool that fails once, and one that fails every time
- when the brief allows several products: each product ends differently (one promised, one refused, one disputed, one already paid); one statement that covers several products at once
- every row of the brief's outcome table should be the expected result of at least one case
- a rule that treats one item of several differently (its own limit, its own deadline, its own minimum): write it twice, once with that item first in the account and once with it last. An agent can get the first item right and the later ones wrong
- a question the brief gives an answer for (is this a robot, is this a scam) put into the SAME sentence as an answer, in the middle of the call and not only at the opening: "I can pay on the sixth. Am I talking to a robot?"
- a value given in two parts over two turns: the script gives the first part, and the second part only when the agent asks for the rest ("say only 'four four'. When asked again, say 'seven one'"). Do this for a year too ("say only 'nineteen'. When asked again, say 'eighty-six'"), and once after the bank has answered with a hint
- a counted question answered with a part missing: asked when to call back, the customer gives only the day; the check says how often the agent may ask ("asks exactly once for another time")
- a message that stops in the middle: "say only: 'I can pay on the twenty'. When the agent asks you to repeat, say the full sentence: ..."
- a date given only in numbers, in the order the brief does NOT read by default, where the brief says what the agent must do then
- dates in the many ways people say them: "the 2nd of January", "the twenty-eighth", "December 28th", "this Saturday", a day of the month with no month; a time as "in twenty minutes" or "half past ten"
- two rules in one sentence: a day that is not allowed together with an amount below the minimum; a hardship together with a date; a callback asked for on a day that is not allowed, with the time missing
- a customer who hangs up in words in the middle of a stage ("I have to go, goodbye") before anything is agreed
- the same wish said in the way that sounds like another one: a lower amount asked as a question ("can we pay half of it?"), which is not a request for new terms; an amount or a date the customer only tells about ("my client owes us forty thousand", "the wedding is on the thirtieth"), which is not an offer; "yes, that is me" followed by a sentence about a busy day, which is not a request to be called back
- a customer who never confirms who they are and only says goodbye, and one who says goodbye in the middle of the callback question
- "I already paid it" with no day given, where the brief wants the day asked

## The customer must not rescue the agent

The script is followed literally by a model. If the script holds the exact final value, the customer will say it when the agent proposes a wrong one, the agent corrects itself, and the case passes although the agent broke the rule. Seen: an agent offered the wrong date for the second product, the scripted customer answered "no, I meant the 26th", and a defect that failed on the platform passed here.

- Where the agent has to work out a value (a deadline, a floor, the next working day), the script says how the customer REACTS, not the value: "accepts whatever date the agent proposes for the loan", "says yes to the first amount the agent names".
- The customer states its own wish once, in its own words, and does not repeat or correct it unless the case is about a correction.
- Then add a check that pins the value: "the loan is offered and read back on 23 October, no other date is proposed for it".

Give 4 to 6 cases a way of talking (`style`): `pieces` (numbers and dates in two parts), `corrects` (says a value wrong and corrects it in the same sentence), `talkative`, `asks_back` (asks a question back before answering), `in_words` (amounts and dates in words).

## The shape of one case

```json
{
 "id": "GEN-01",
 "brief": "A",
 "family": "Dates",
 "level": "hard",
 "title": "Names a bank holiday",
 "variables": {"first_name": "...", "last_name": "...", "title": "Mr", "now": "2026-10-14T11:00"},
 "identity": {"<security field of the tool API>": "<the right answer>", "...": "..."},
 "products": [{"type": "...", "past_due": 0.0, "days_late": 0, "...": "..."}],
 "script": "What this customer says and does, in the order it happens. ...",
 "expected": {"outcome": "...", "reason": null, "promises": [{"product_id": "p1", "amount": 0.0, "date": "YYYY-MM-DD"}], "notes": [{"product_id": "p2", "reason": "..."}], "callbacks": [{"date": "YYYY-MM-DD", "time": "HH:MM"}], "transfers": [{"queue": "..."}]},
 "checks": ["...", "..."],
 "third_party": false,
 "faults": [{"tool": "create_promise", "fail_first_n": 1}],
 "style": "corrects"
}
```

- `brief`: the letter you were told to use. `level`: easy, medium or hard. `family`: a short label.
- `variables`: the call variables the brief lists, except the customer id (it is added by the test).
- `identity`: one entry per security question, keyed by the field name the identity tool takes. A date answer as `YYYY-MM-DD`.
- `products`: what the account tool returns, in order. The test bank numbers them `p1`, `p2`, ... Use the product fields the brief names (type, past-due amount, days late, the field that gives the minimum).
- `script`: the customer is played by a model that follows this text literally and invents nothing. Write every date, amount and sentence it needs. Say what it answers when the agent offers something. If the person on the line is not the customer, set `"third_party": true` and say in the script who they are. A customer whose identity passes: start with "Verified." or say which answers it gives wrong and in what order.
- `expected`: the bank's records after a call that followed the brief exactly. `outcome` and `reason` from the brief's outcome table (`reason` null when the table gives none). `promises`, `notes`, `callbacks`, `transfers`: the exact set, empty lists when there are none. Work out every date and amount with the brief's rules from `now` and the product; write the rule numbers you used into a `"derivation"` field so a reader can check you.
- `checks`: 2 to 4 short statements a judge can verify from the transcript and the tool calls alone, without the brief. Each is one of: an order ("no amount is stated before both security questions passed"), a count ("the phone question is asked exactly twice"), a required sentence, a forbidden word or offer, honesty after a failed tool. Write only what the brief requires. Do not write a check you are not sure the brief demands.
- `faults`: optional. `fail_first_n` calls of that tool fail with a service error.
- Leave out `third_party`, `faults` and `style` when they do not apply.

## What the test bank does

- It checks the security answers of `identity`. A date said day-and-month swapped gets the hint the brief describes and is not counted as wrong. After three wrong answers in total it refuses further checks.
- It returns `products` only after all answers passed.
- It records whatever it is sent: a promise on a holiday or below a minimum is accepted. Keeping the brief is the agent's job, and `expected` is what a correct agent leaves behind.
- The call must end with the closing tool as the last tool call.

## Before you finish

Go through every case once more against the brief: is the expected outcome the only one the brief allows for this script? If two readings are possible, change the script until only one is. A wrong expectation makes a correct agent fail and misleads the repair.
