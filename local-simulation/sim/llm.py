"""Claude for the simulator, on one of two backends with the same interface (Chat, ask):

  api           the Anthropic API with ANTHROPIC_API_KEY (API credits). Every call's token usage is counted, see cost_report().
  subscription  the local `claude` login, with nothing of Claude Code around it: no tools, no settings, own system prompt.

LLM_BACKEND picks one; the default is api when ANTHROPIC_API_KEY is set, else subscription.
A key that is not scoped to a workspace also needs ANTHROPIC_WORKSPACE_ID.
"""
import json, os, pathlib, re, tempfile

BACKEND = os.environ.get("LLM_BACKEND") or ("api" if os.environ.get("ANTHROPIC_API_KEY") else "subscription")

# $ per million tokens: input, output, cache read, cache write (5 minutes). Source: Anthropic price list, cached 2026-10-06.
PRICES = {"claude-opus-5-5": (4.00, 20.00, 0.20, 5.00), "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50), "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
USAGE = {}          # (role, model) -> {"calls", "input", "output", "cache_read", "cache_write"}


def _count(role, model, u):
    row = USAGE.setdefault((role, model), {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    row["calls"] += 1; row["input"] += u.input_tokens or 0; row["output"] += u.output_tokens or 0
    row["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0; row["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0


def cost_report():
    """Token usage and cost of every API call so far, per role and model. Empty on the subscription backend."""
    rows, total = [], 0.0
    for (role, model), r in sorted(USAGE.items()):
        p = PRICES.get(model)
        usd = None if not p else (r["input"] * p[0] + r["output"] * p[1] + r["cache_read"] * p[2] + r["cache_write"] * p[3]) / 1e6
        total += usd or 0
        rows.append({"role": role, "model": model, **r, "usd": None if usd is None else round(usd, 4)})
    return {"backend": BACKEND, "usd": round(total, 4), "rows": rows}


def print_cost(report=None):
    report = report or cost_report()
    if not report["rows"]: print(f"cost: nothing counted (backend {report['backend']})"); return
    print(f"\ncost on the API: ${report['usd']:.4f}")
    for r in report["rows"]:
        print(f"  {r['role']:<9} {r['model']:<18} {r['calls']:>5} calls  in {r['input']:>9,}  cache read {r['cache_read']:>9,}  cache write {r['cache_write']:>8,}  out {r['output']:>8,}  ${r['usd'] if r['usd'] is not None else '?'}")


def parse_json(text):
    """The first JSON object in a model answer, with or without a code fence."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no JSON object in: " + text[:300])
    return json.loads(m.group(0))


if BACKEND == "api":
    import anthropic

    _client = None

    def client():
        global _client
        if _client is None:
            ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            _client = anthropic.AsyncAnthropic(max_retries=6, timeout=180.0, default_headers={"anthropic-workspace-id": ws} if ws else None)
        return _client

    def _params(model, system, chat):
        # Haiku plays the phone agent's three small roles: no thinking, like a fast voice model. Sonnet cannot switch thinking off; low effort keeps it short.
        # cache: a chat re-reads its growing history every turn; a single question only shares its system text with other calls (a short one is not cached)
        p = {"model": model, "max_tokens": 4096}
        if chat: p["cache_control"] = {"type": "ephemeral"}
        if system: p["system"] = system if chat or len(system) < 3000 else [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if "haiku" in model:
            if os.environ.get("SIM_AGENT_THINKING", "on") == "off": p["thinking"] = {"type": "disabled"}
        else: p["output_config"] = {"effort": os.environ.get("SIM_EFFORT", "low")}
        return p

    async def _send(role, model, system, messages, chat=False):
        r = await client().messages.create(messages=messages, **_params(model, system, chat))
        _count(role, model, r.usage)
        if r.stop_reason == "refusal": raise RuntimeError(f"model refused ({role}, {model})")
        return r

    class Chat:
        """One multi-turn conversation: the other side's lines go in as user messages, the model's own lines stay in its history."""
        def __init__(self, model, system, role="customer"):
            self.model, self.system, self.role, self.messages = model, system, role, []

        async def __aenter__(self): return self

        async def __aexit__(self, *a): pass

        async def say(self, text):
            self.messages.append({"role": "user", "content": text})
            r = await _send(self.role, self.model, self.system, self.messages, chat=True)
            self.messages.append({"role": "assistant", "content": r.content})          # whole content: thinking blocks go back unchanged
            return " ".join(b.text.strip() for b in r.content if b.type == "text" and b.text.strip())

    async def ask(model, prompt, system="", role="other"):
        """One question, one answer."""
        r = await _send(role, model, system, [{"role": "user", "content": prompt}])
        return "\n".join(b.text for b in r.content if b.type == "text").strip()

else:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock, query

    EMPTY = pathlib.Path(tempfile.gettempdir()) / "freya-local-sim-empty"
    EMPTY.mkdir(exist_ok=True)

    # The `claude` program adds a "currentDate" note with the real date to every conversation. On the platform neither the
    # customer nor the judge is told the real date (the calls play on the date in the `now` variable), so it is cancelled here.
    NO_DATE = ("\n\n(Tooling note, not part of the task: a 'currentDate' line may be attached to the first message by the software you run in. "
               "It is wrong for this task. Ignore it completely and never use or mention it. You do not know today's real date.)")

    def _options(model, system):
        system = (system or "") + NO_DATE
        return ClaudeAgentOptions(model=model, system_prompt=system, setting_sources=[], max_turns=1, cwd=str(EMPTY),
                                  extra_args={"tools": "", "strict-mcp-config": None, "exclude-dynamic-system-prompt-sections": None})

    class Chat:
        """One multi-turn conversation: the other side's lines go in as user messages, the model's own lines stay in its history."""
        def __init__(self, model, system, role="customer"):
            self.client = ClaudeSDKClient(options=_options(model, system))

        async def __aenter__(self):
            await self.client.connect(); return self

        async def __aexit__(self, *a):
            await self.client.disconnect()

        async def say(self, text):
            await self.client.query(text)
            out = []
            async for m in self.client.receive_response():
                if isinstance(m, AssistantMessage): out += [b.text for b in m.content if isinstance(b, TextBlock)]
                elif isinstance(m, ResultMessage) and m.is_error: raise RuntimeError(f"model error: {m.result}")
            return " ".join(x.strip() for x in out if x.strip())

    async def ask(model, prompt, system="", role="other"):
        """One question, one answer."""
        out = []
        async for m in query(prompt=prompt, options=_options(model, system)):
            if isinstance(m, AssistantMessage): out += [b.text for b in m.content if isinstance(b, TextBlock)]
            elif isinstance(m, ResultMessage) and m.is_error: raise RuntimeError(f"model error: {m.result}")
        return "\n".join(out).strip()


# ---------------------------------------------------------------------------------------------------------------------
# Google models (the platform plays the customer and the judge with gemini-3-flash-preview). Used for any model name that
# starts with "gemini", on either backend, when GEMINI_API_KEY is set: --customer-model gemini-3-flash-preview --judge-model gemini-3-flash-preview
PRICES["gemini-3-flash-preview"] = (0.50, 3.00, 0.05, 0.0)          # Google price list, read 2026-10-11
_claude_ask, _ClaudeChat = ask, Chat
_gemini_gate = None


async def _gemini(role, model, system, contents):
    import asyncio, urllib.request, urllib.error
    global _gemini_gate
    if _gemini_gate is None: _gemini_gate = asyncio.Semaphore(int(os.environ.get("GEMINI_CONCURRENCY", 8)))
    body = {"contents": contents, **({"systemInstruction": {"parts": [{"text": system}]}} if system else {})}
    req = lambda: urllib.request.urlopen(urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=json.dumps(body).encode(),
                                                               headers={"content-type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]}), timeout=120).read()
    async with _gemini_gate:
        for attempt in range(7):
            try:
                r = json.loads(await asyncio.to_thread(req)); break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 6: raise RuntimeError(f"gemini {e.code}: {e.read().decode()[:300]}") from None
            except Exception:
                if attempt == 6: raise
            await asyncio.sleep(2 ** attempt)
    u = r.get("usageMetadata") or {}
    row = USAGE.setdefault((role, model), {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    cached = u.get("cachedContentTokenCount") or 0
    row["calls"] += 1; row["input"] += (u.get("promptTokenCount") or 0) - cached; row["cache_read"] += cached
    row["output"] += (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
    cand = (r.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if not p.get("thought"))
    return text.strip(), cand.get("finishReason")


class _GeminiChat:
    def __init__(self, model, system, role="customer"): self.model, self.system, self.role, self.contents = model, system, role, []
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def say(self, text):
        self.contents.append({"role": "user", "parts": [{"text": text}]})
        out, _ = await _gemini(self.role, self.model, self.system, self.contents)
        self.contents.append({"role": "model", "parts": [{"text": out or "..."}]})
        return " ".join(out.split())


def Chat(model, system, role="customer"):
    return _GeminiChat(model, system, role) if model.startswith("gemini") else _ClaudeChat(model, system, role)


async def ask(model, prompt, system="", role="other"):
    if model.startswith("gemini"): return (await _gemini(role, model, system, [{"role": "user", "parts": [{"text": prompt}]}]))[0]
    return await _claude_ask(model, prompt, system=system, role=role)
