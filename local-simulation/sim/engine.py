"""A local stand-in for Freya's workflow engine: runs a workflow JSON turn by turn against a local bank.

It follows kit docs/workflow-format.md and docs/tools-and-code.md: one variable table, node entry (fixed line, engine-run
calls, code), routing (deterministic conditions, intents, tool-call transitions, passthrough chains, routing again after a
tool result), tools with baked and model-filled parameters, pre and post hooks, response mapping and the automatic result
keys, end_call. Events are written in the platform's own shapes so the judge reads the same kind of record.

It is an imitation. The three model roles (dialogue, routing, extraction) are played by a Claude model, not by the
platform's model, and wherever the docs are silent the behaviour here is a guess. Known gaps: confidence scores,
truncation, background tools, loop prompts, voice settings.
"""
import json, re

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
DIALOGUE_PROTOCOL = """

---
OUTPUT FORMAT (how this runtime reads your reply; it is not part of your instructions above).
Reply with exactly one JSON object and nothing else:
{"say": "<what you say to the customer now, or an empty string>", "tool": null}
or, to call a tool:
{"say": "<optional words spoken before the tool runs>", "tool": {"name": "<tool name>", "arguments": {<arguments>}}}
When you call a tool, leave "say" empty unless your instructions tell you to say something before the tool runs: you speak after you have seen the result.
You can call only the tools listed here. If the list is empty you cannot call a tool.
TOOLS: """


class Engine:
    def __init__(self, workflow, variables, bank, ask):
        wf = workflow.get("workflow", workflow)
        self.wf, self.nodes, self.bank, self.ask = wf, wf["nodes"], bank, ask
        g = wf.get("global_nodes") or {}
        self.globals = g if isinstance(g, dict) else {x["name"]: x for x in g}
        self.vars = {**(wf.get("test_variables") or {}), **variables}
        self.messages, self.history = [], []            # platform-shaped log; what the dialogue model sees
        self.node, self.prev, self.entered, self.turns, self.total = None, None, set(), {}, {}
        self.model_fields = {}                          # node -> tool -> fields the model fills (decided when the node is entered)
        self.ended, self.end_reason, self.spoke, self.unread, self.hops = False, None, False, [], 0
        self.mapped = {}                                                  # variable -> last value a response mapping gave it

    # ------------------------------------------------------------------ log
    def event(self, **e): self.messages.append({"role": "system", "message": json.dumps(e, default=str, ensure_ascii=False), "nodeId": self.node})

    def say(self, text):
        text = (text or "").strip()
        if not text: return
        self.messages.append({"role": "assistant", "message": text, "nodeId": self.node})
        self.history.append(("assistant", text)); self.unread.append(text); self.spoke = True

    def take_reply(self):
        out, self.unread = " ".join(self.unread), []
        return out

    # ------------------------------------------------------------------ variables
    def is_set(self, name): return self.vars.get(name) not in (None, "")

    def sub(self, text):
        if not isinstance(text, str): return text
        def rep(m):
            name, _, fmt = m.group(1).partition(":")
            name = name.strip()
            if name.endswith(".current_turns") and name[:-14] in self.nodes: return str(self.turns.get(name[:-14], 0))
            if name.endswith(".total_turns") and name[:-12] in self.nodes: return str(self.total.get(name[:-12], 0))
            if not self.is_set(name) and name not in self.vars: return m.group(0)        # an unresolved placeholder stays as literal text
            v = self.vars.get(name)
            return json.dumps(v) if fmt.strip() == "json" else ("" if v is None else str(v))
        return PLACEHOLDER.sub(rep, text)

    def flatten(self, prefix, obj):
        self.vars[prefix] = obj
        if isinstance(obj, dict):
            for k, v in obj.items(): self.flatten(f"{prefix}.{k}", v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj): self.flatten(f"{prefix}.{i}", v)
        elif isinstance(obj, bool): self.vars[prefix] = "true" if obj else "false"     # booleans from results arrive as strings

    # ------------------------------------------------------------------ conditions
    @staticmethod
    def intents_of(c):
        if not c: return []
        if c.get("operator") == "matches_intent": return [c.get("description") or ""]
        return [d for x in c.get("conditions") or [] for d in Engine.intents_of(x)]

    def holds(self, c, intents=None):
        if not c: return True
        if "conditions" in c:
            r = [self.holds(x, intents) for x in c["conditions"]]
            return all(r) if c.get("logic") == "and" else any(r)
        op = c.get("operator")
        if op == "always": return True
        if op == "matches_intent": return bool((intents or {}).get(c.get("description") or ""))
        v, operand = self.vars.get(c.get("variable")), c.get("operand")
        if op == "is_set": return v not in (None, "")
        if op == "is_not_set": return v in (None, "")
        if op == "equals": return v is not None and str(v) == str(operand)
        if op == "not_equals": return v is None or str(v) != str(operand)
        if op == "contains": return v is not None and str(operand).lower() in str(v).lower()
        if op in ("greater_than", "less_than"):
            try: a, b = float(v), float(operand)
            except Exception: return False
            return a > b if op == "greater_than" else a < b
        return False

    async def judge_intents(self, descs, node):
        """One routing-model call for all intent conditions of a routing step. It sees recent messages and the descriptions, not the dialogue prompt."""
        descs = list(dict.fromkeys(self.sub(d) for d in descs))
        last = next((t for r, t in reversed(self.history) if r == "user"), "")
        convo = "\n".join(f"{'Agent' if r == 'assistant' else 'Customer'}: {t}" for r, t in [h for h in self.history if h[0] in ("user", "assistant")][-10:])
        extra = self.sub(node.get("routing_prompt") or "Decide for each numbered condition whether the customer's LAST message matches it.")
        prompt = (f"You are the routing model of a phone agent.\n{extra}\n\nRecent conversation:\n{convo}\n\nThe customer's last message: {last!r}\n\nConditions:\n"
                  + "\n".join(f"{i + 1}. {d}" for i, d in enumerate(descs))
                  + '\n\nAnswer with one JSON object mapping each number to true or false, for example {"1": false, "2": true}. Nothing else.')
        try: got = parse_json(await self.ask(prompt, "You output only JSON."))
        except Exception: got = {}
        return {d: bool(got.get(str(i + 1))) for i, d in enumerate(descs)}

    # ------------------------------------------------------------------ routing
    async def route(self, after_tool=False):
        """Return (target, reason) or (None, reason). After a tool result only deterministic conditions are looked at."""
        node = self.nodes[self.node]
        trs = [t for t in node.get("transitions") or [] if not t.get("is_tool_call")]
        if not after_tool and node.get("max_turn_transition") and self.turns.get(self.node, 0) > int(node.get("max_node_turns") or 10):
            return node["max_turn_transition"], "max_node_turns reached"
        plain = lambda t: not self.intents_of(t.get("condition")) and (t.get("condition") or {}).get("operator") != "always"
        for t in trs:                                               # deterministic conditions first: no model call when one matches
            if plain(t) and self.holds(t.get("condition")): return t["target_node"], self.sub(t.get("description") or "")
        intents = None
        if not after_tool:
            descs = [d for t in trs for d in self.intents_of(t.get("condition"))] + ([node["loop_condition"]] if node.get("loop_condition") else [])
            if descs: intents = await self.judge_intents(descs, node)
            if node.get("loop_condition") and intents.get(self.sub(node["loop_condition"])): return None, "loop_condition"
        for t in trs:                                               # then list order
            if self.intents_of(t.get("condition")) and after_tool: continue
            if self.holds(t.get("condition"), self._subbed(intents)): return t["target_node"], self.sub(t.get("description") or "")
        if not after_tool:
            for name, g in self.globals.items():
                if self.node in (g.get("excluded_nodes") or []) or name == self.node or g.get("is_tool_call"): continue
                gi = await self.judge_intents(self.intents_of(g.get("condition")), g) if self.intents_of(g.get("condition")) else None
                if g.get("condition") and self.holds(g["condition"], self._subbed(gi)): return name, self.sub(g.get("description") or "global node")
        return None, "No transition matched"

    def _subbed(self, intents):
        """Intent results are keyed by the substituted description; conditions carry the raw one."""
        if intents is None: return None
        class M(dict):
            def get(s, k, d=None): return dict.get(s, self.sub(k), d)
        return M(intents)

    async def go(self, target, reason, from_tool=None):
        if target in (None, self.node): return False               # a transition to the node itself means: stay
        if target == "__previous__": target = self.prev
        if target not in self.nodes and target not in self.globals:
            self.event(event="error", message=f"transition to unknown node {target}"); return False
        if from_tool: self.event(event="tool_call_routing", tool_name=from_tool, from_node=self.node, target=target)
        else: self.event(event="route_evaluated", matched=True, from_node=self.node, target=target, confidence=1.0, reason=reason)
        await self.enter(target, reason if not from_tool else f"Tool call: {from_tool}")
        return True

    # ------------------------------------------------------------------ node entry
    def node_def(self, name): return self.nodes.get(name) or self.globals[name]

    async def enter(self, name, reason="start"):
        self.hops += 1
        if self.hops > 40:
            self.event(event="error", message="more than 40 step changes in one turn, stopping (loop)"); return
        frm, self.prev, self.node = self.node, self.node, name
        n = self.node_def(name)
        first = name not in self.entered
        self.entered.add(name); self.turns[name] = 0
        self.event(event="node_enter", node=name, **({"from_node": frm, "reason": reason} if frm else {}))
        kind = n.get("node_type", "conversation")
        # which parameters the model fills is decided now: a {{var}} default that has a value at this moment is baked
        self.model_fields[name] = {t["function"]["name"]: self._model_fields(t) for t in n.get("tools") or []}
        oe = n.get("on_enter") or {"type": "llm"}
        speak = first or not n.get("on_enter_first_entry_only")
        if oe.get("type") == "tts" and speak: self.say(self.sub(oe.get("text") or ""))
        if kind == "end_call": return await self.terminal(n)
        if kind in ("api_request", "code"):
            for pa in n.get("pre_actions") or []:
                if self.ended: return
                res = await self.run_tool(pa, {}, owner=name, node_mapping=n.get("response_mapping"))
                self.history.append(("tool_call", pa["function"]["name"], {})); self.history.append(("tool_result", pa["function"]["name"], self._shown(res, n)))
        if n.get("passthrough") or kind in ("api_request", "code"):
            target, why = await self.route(after_tool=True)
            if target is None or target == name: target, why = n.get("passthrough_default_transition"), "passthrough default"
            if not target or target == name:
                self.event(event="error", message=f"passthrough node {name} has no matching transition: the call is stuck"); return
            self.event(event="route_evaluated", matched=True, from_node=name, target=target, confidence=1.0, reason=why)
            return await self.enter(target, why)
        if oe.get("type") == "llm" and speak: return await self.dialogue(entry=oe.get("prompt") or "You have just reached this step. Say your first line for it now.")
        if not self.spoke and self.history and self.history[-1][0] != "assistant":      # entered silently in the middle of a turn: the customer still needs an answer
            return await self.dialogue()

    async def terminal(self, n):
        p = ((n.get("terminal") or {}).get("function") or {}).get("parameters", {}).get("properties", {})
        mode = (p.get("message_mode") or {}).get("default", "custom")
        self.event(event="function_call_start", function="end_call", args={"message_mode": mode})
        if mode == "custom": self.say(self.sub((p.get("spoken_message") or {}).get("default", "")))
        elif mode == "llm": await self.dialogue(entry=self.sub((p.get("llm_prompt") or {}).get("default", "Say goodbye.")), no_tools=True)
        await self.finish("assistant-ended-call")

    async def finish(self, reason):
        if self.ended: return
        self.ended, self.end_reason = True, reason
        self.event(event="call_ended", reason=reason)
        for t in self.wf.get("tools") or []:                        # lifecycle tools run by the engine after the call
            if "end_of_call" in ((t.get("config") or {}).get("alwaysRunsAt") or []): await self.run_tool(t, await self.fill(t), owner=t["function"]["name"])

    async def fill(self, t):
        """A lifecycle tool with parameters that no variable resolves: one model call fills them from the conversation."""
        fields = self._model_fields(t)
        if not fields: return {}
        f = t["function"]
        props = {shown: {x: y for x, y in spec.items() if x in ("type", "description", "enum")} for shown, (field, spec) in fields.items()}
        prompt = ("Conversation so far:\n" + self.render_history() + f"\n\nThe call has ended. Fill the arguments of the tool {f['name']} ({self.sub(f.get('description') or '')}) "
                  f"from this conversation. Parameters: {json.dumps(props)}. Answer with one JSON object of arguments. Nothing else.")
        try: return parse_json(await self.ask(prompt, self.system_message(self.node_def(self.node))))
        except Exception: return {}

    # ------------------------------------------------------------------ a customer message
    async def start(self):
        for t in self.wf.get("tools") or []:
            if "start_of_call" in ((t.get("config") or {}).get("alwaysRunsAt") or []): await self.run_tool(t, {}, owner=t["function"]["name"])
        await self.enter(self.wf["initial_node"])

    async def user(self, text):
        if self.ended: return
        self.spoke, self.hops = False, 0
        self.messages.append({"role": "user", "message": text, "nodeId": self.node}); self.history.append(("user", text))
        self.turns[self.node] = self.turns.get(self.node, 0) + 1; self.total[self.node] = self.total.get(self.node, 0) + 1
        n = self.node_def(self.node)
        if n.get("extraction_schema"): await self.extract(n, text)
        target, why = await self.route()
        if target and target != self.node:
            await self.go(target, why)
            if not self.ended and not self.spoke: await self.dialogue()
            return
        self.event(event="route_evaluated", matched=False, from_node=self.node, confidence=0.0, reason=why)
        await self.dialogue()

    async def extract(self, n, text):
        schema = n["extraction_schema"]
        convo = "\n".join(f"{'Agent' if r == 'assistant' else 'Customer'}: {t}" for r, t in [h for h in self.history if h[0] in ("user", "assistant")][-10:])
        lines = []
        for e in schema:
            lines.append(f"- {e['name']} ({e.get('type', 'string')}" + (f", one of {e['enum_values']}" if e.get("enum_values") else "") + f"): {self.sub(e.get('description') or '')}")
        custom = "\n".join(self.sub(e["extraction_prompt"]).replace("{{extractor.speech}}", text).replace("{{extractor.conversation.last-10}}", convo)
                           .replace("{{extractor.variables_list}}", "\n".join(lines)).replace("{{extractor.type_preamble}}", "") for e in schema if e.get("extraction_prompt"))
        prompt = (f"You are the extraction model of a phone agent. Extract the variables below from the customer's last message.\n\nRecent conversation:\n{convo}\n\n"
                  f"The customer's last message: {text!r}\n\nVariables:\n" + "\n".join(lines) + (f"\n\nExtra instructions:\n{custom}" if custom else "")
                  + "\n\nAnswer with one JSON object: variable name -> value, or null when the message does not give it. Nothing else.")
        try: got = parse_json(await self.ask(prompt, "You output only JSON."))
        except Exception: got = {}
        for e in schema:
            v = got.get(e["name"])
            if v in (None, ""): continue
            if e.get("enum_values") and v not in e["enum_values"]: continue
            if e.get("validation_pattern") and not re.fullmatch(e["validation_pattern"], str(v)): continue
            self.vars[e["name"]] = v
            self.event(event="variable_extracted", name=e["name"], value=v, confidence=1.0, source="extraction", source_node=self.node, input_text=text)

    # ------------------------------------------------------------------ dialogue model
    def system_message(self, n):
        parts = [self.sub(self.wf.get("system_prompt") or ""), self.sub(n.get("role_message") or ""), "## Current Task\n" + self.sub(n.get("task_message") or "")]
        return "\n\n".join(p for p in parts if p.strip())

    def tool_specs(self, n):
        specs = []
        for t in n.get("tools") or []:
            f = t["function"]; fields = self.model_fields.get(self.node, {}).get(f["name"], {})
            body = ((f.get("parameters") or {}).get("properties") or {}).get("body") or {}
            if f.get("handler", "").endswith("code_exec") or f.get("handler", "").endswith("end_call"):
                props = {k: {x: y for x, y in v.items() if x != "default"} for k, v in ((f.get("parameters") or {}).get("properties") or {}).items() if "default" not in v}
                req = (f.get("parameters") or {}).get("required") or []
            else:
                props = {shown: {x: y for x, y in spec.items() if x in ("type", "description", "enum")} for shown, (field, spec) in fields.items()}
                req = [shown for shown, (field, spec) in fields.items() if field in (body.get("required") or [])]
            specs.append({"name": f["name"], "description": self.sub(f.get("description") or ""), "parameters": {"type": "object", "properties": props, "required": req}})
        for t in (n.get("transitions") or []) + [dict(g, target_node=k) for k, g in self.globals.items() if g.get("is_tool_call") and self.node not in (g.get("excluded_nodes") or [])]:
            if t.get("is_tool_call"):
                specs.append({"name": "transition_to_" + t["target_node"], "description": self.sub(t.get("description") or (t.get("condition") or {}).get("description") or ""), "parameters": {"type": "object", "properties": {}}})
        return specs

    def render_history(self):
        out = []
        for h in self.history:
            if h[0] == "assistant": out.append("Agent (you): " + h[1])
            elif h[0] == "user": out.append("Customer: " + h[1])
            elif h[0] == "tool_call": out.append(f"[you called the tool {h[1]} with {json.dumps(h[2], ensure_ascii=False)}]")
            else: out.append(f"[result of {h[1]}: {json.dumps(h[2], ensure_ascii=False, default=str)[:3000]}]")
        return "\n".join(out) or "(The call has just connected. Nobody has spoken yet.)"

    async def dialogue(self, entry=None, no_tools=False):
        for _ in range(8):
            if self.ended: return
            n = self.node_def(self.node)
            specs = [] if no_tools else self.tool_specs(n)
            prompt = "Conversation so far:\n" + self.render_history() + (f"\n\n[Instruction for this moment: {self.sub(entry)}]" if entry else "") + "\n\nReply now with the JSON object."
            raw = await self.ask(prompt, self.system_message(n) + DIALOGUE_PROTOCOL + json.dumps(specs, ensure_ascii=False))
            try: out = parse_json(raw)
            except Exception: out = {"say": raw.strip(), "tool": None}
            say, tool, entry = out.get("say") or "", out.get("tool"), None
            self.say(say)
            if not tool or not isinstance(tool, dict) or not tool.get("name"):
                if not say.strip(): self.event(event="error", message="The model returned no speakable content")
                return
            name, args = tool["name"], tool.get("arguments") or {}
            self.history.append(("tool_call", name, args))
            if name.startswith("transition_to_"):
                target = name[len("transition_to_"):]
                ok = any(t.get("is_tool_call") and t["target_node"] == target for t in n.get("transitions") or []) or (self.globals.get(target) or {}).get("is_tool_call")
                if ok:
                    self.history.append(("tool_result", name, {"status": "success"}))
                    await self.go(target, "", from_tool=name)
                    if not self.ended and not self.spoke: continue
                    return
            td = next((t for t in n.get("tools") or [] if t["function"]["name"] == name), None)
            if not td:
                self.history.append(("tool_result", name, f"The function `{name}` is not currently available.")); continue
            if td["function"].get("handler", "").endswith("end_call"):
                self.event(event="function_call_start", function="end_call", args=args); self.say(args.get("spoken_message") or "")
                return await self.finish("assistant-ended-call")
            res = await self.run_tool(td, args, owner=name)
            self.history.append(("tool_result", name, self._shown(res, n)))
            target, why = await self.route(after_tool=True)          # routing runs again after a tool result, in the same turn
            if target and target != self.node:
                await self.go(target, why)
                if not self.ended and not self.spoke: continue
                return
        self.event(event="error", message="the dialogue model did not finish its turn in 8 steps")

    @staticmethod
    def _shown(res, n):
        if n.get("suppress_tool_results"): return {"status": (res or {}).get("status")}
        return {k: v for k, v in res.items() if k != "header"} if isinstance(res, dict) else res

    # ------------------------------------------------------------------ tools
    def _model_fields(self, t):
        """shown name -> (body field, spec) for the parameters the model fills."""
        body = (((t["function"].get("parameters") or {}).get("properties") or {}).get("body") or {}).get("properties") or {}
        out = {}
        for field, spec in body.items():
            d = spec.get("default")
            if "default" not in spec: out[field] = (field, spec)
            elif isinstance(d, str) and (m := PLACEHOLDER.fullmatch(d.strip())) and not self.is_set(m.group(1).strip()): out[m.group(1).strip()] = (field, spec)
        return out

    def _coerce(self, v, spec):
        try:
            if spec.get("type") == "number": return float(v)
            if spec.get("type") == "integer": return int(float(v))
            if spec.get("type") == "boolean" and isinstance(v, str): return v.lower() == "true"
        except Exception: pass
        return v

    def _run_code(self, code, env, what):
        before = dict(self.vars)
        try: exec(code, {"__builtins__": __builtins__, **env})
        except Exception as e:
            self.event(event="error", message=f"{what} raised {type(e).__name__}: {e}"); return e
        finally:
            # seen on the platform (11 Oct): a value code writes to an enum extraction variable that is not one of its values is rejected, the old value stays
            for name, allowed in self.enums().items():
                if name in self.vars and self.vars[name] != before.get(name) and self.vars[name] not in allowed:
                    self.event(event="error", message=f"variable '{name}' rejected by schema validation: {self.vars[name]!r}")
                    if name in before: self.vars[name] = before[name]
                    else: del self.vars[name]
            # seen on the platform (fy-a, PUB-04 and PUB-07, 11 Oct): code cleared an integer and a float extraction variable with '' ; the write was
            # rejected ("rejected by schema validation"), the old day stayed, and every later "yes" was read as the old proposal again
            for name, kind in self.typed().items():
                v = self.vars.get(name)
                if name in self.vars and v != before.get(name) and not (isinstance(v, bool) if kind == "boolean" else isinstance(v, int) and not isinstance(v, bool) if kind == "integer" else isinstance(v, (int, float)) and not isinstance(v, bool)):
                    self.event(event="error", message=f"variable '{name}' rejected by schema validation: {v!r} is not {kind}")
                    if name in before: self.vars[name] = before[name]
                    else: del self.vars[name]
        return None

    def typed(self):
        if not hasattr(self, "_typed"):
            self._typed = {e["name"]: e["type"] for n in (self.nodes or {}).values() for e in n.get("extraction_schema") or [] if e.get("type") in ("integer", "float", "number", "boolean")}
        return self._typed

    def enums(self):
        if not hasattr(self, "_enums"):
            self._enums = {}
            for n in (self.nodes or {}).values():
                for e in n.get("extraction_schema") or []:
                    if e.get("enum_values"): self._enums.setdefault(e["name"], set()).update(e["enum_values"])
        return self._enums

    async def run_tool(self, t, args, owner, node_mapping=None):
        f = t["function"]; name = f["name"]; props = (f.get("parameters") or {}).get("properties") or {}
        keys = {owner, name}
        if f.get("handler", "").endswith("code_exec"):
            code = (props.get("code") or {}).get("default") or args.get("code") or ""
            self.event(event="function_call_start", function="code_exec", args={"timeout_ms": (props.get("timeout_ms") or {}).get("default"), "code": code})
            err = self._run_code(code, {"vars": self.vars}, "code")
            res = {"status": "success", "data": None, "message": "Code executed successfully", "status_code": 200} if not err else {"status": "error", "data": None, "message": str(err), "status_code": 500}
            self.event(event="function_call_complete", function="code_exec", result=res)
            return self._record({owner}, res, None, ok=not err)
        cfg = {"url": self.sub((props.get("url") or {}).get("default", "")), "method": (props.get("method") or {}).get("default", "POST"),
               "headers": {k: self.sub(v.get("default", "")) for k, v in ((props.get("headers") or {}).get("properties") or {}).items()}, "body": {}}
        fields = self._model_fields(t) if owner not in self.model_fields.get(self.node, {}) else self.model_fields[self.node][owner]
        by_field = {field: shown for shown, (field, spec) in fields.items()}
        flat = {**args, **(args.get("body") if isinstance(args.get("body"), dict) else {})}
        for field, spec in ((props.get("body") or {}).get("properties") or {}).items():
            if field in by_field:                                   # the model fills it
                if "default" not in spec: continue                  # measured on the platform (11 Oct): a body field without a default never reaches the request
                v = flat.get(by_field[field], flat.get(field))
                if v is not None: cfg["body"][field] = self._coerce(v, spec)
            else:
                d = spec.get("default")
                if isinstance(d, str) and (m := PLACEHOLDER.fullmatch(d.strip())):
                    if self.is_set(m.group(1).strip()): cfg["body"][field] = self._coerce(self.vars[m.group(1).strip()], spec)     # an unset one is dropped from the request
                elif d is not None: cfg["body"][field] = self.sub(d) if isinstance(d, str) else d
        if (t.get("pre_process_code") or "").strip(): self._run_code(t["pre_process_code"], {"vars": self.vars, "config": cfg}, f"pre hook of {name}")
        self.event(event="function_call_start", function=name if owner == name else "api_call", args=cfg)
        retries = int((t.get("config") or {}).get("maxRetries") or 0)
        blocked = not cfg.get("url")      # seen on the platform (11 Oct): a pre hook that empties the url gives this error, the post hook does not run, and the model is left to improvise
        if blocked: status, data, res = 0, None, {"status": "error", "data": None, "message": "Missing required field: url", "status_code": 0}
        else:
            for _ in range(retries + 1):
                status, data = self.bank.http(cfg["url"], cfg.get("method"), cfg.get("body"))
                if status < 500: break
            res = ({"status": "success", "data": data, "message": f"Successfully executed {cfg.get('method')} request", "status_code": status, "header": {"Content-Type": "application/json"}} if status < 400
                   else {"status": "error", "data": data, "message": f"API request failed: {data}", "status_code": status})
        ok = res["status"] == "success"
        if not ok: self.event(event="function_call_error", function=name if owner == name else "api_call", error=res["message"])
        if (t.get("post_process_code") or "").strip() and not blocked: self._run_code(t["post_process_code"], {"vars": self.vars, "result": res}, f"post hook of {name}")
        if ok: self.event(event="function_call_complete", function=name if owner == name else "api_call", result={k: v for k, v in res.items() if k != "header"})
        for k in keys: self.flatten(f"{k}_args", {**cfg, **(cfg.get("body") if isinstance(cfg.get("body"), dict) else {})})
        return self._record(keys, res, (t.get("response_mapping") or []) + (node_mapping or []), ok)

    def _record(self, keys, res, mapping, ok):
        for k in keys:
            self.flatten(f"{k}_response", res)
            self.vars[f"{k}_status_code"] = res.get("status_code")
            c = f"{k}_success_count" if ok else f"{k}_failure_count"
            self.vars[c] = int(self.vars.get(c) or 0) + 1
        for m in mapping or []:
            v = res
            for part in str(m.get("path") or "").split("."):
                v = v.get(part) if isinstance(v, dict) else (v[int(part)] if isinstance(v, list) and part.isdigit() and int(part) < len(v) else None)
                if v is None: break
            if v is not None: self.vars[m["variable"]] = self.mapped[m["variable"]] = ("true" if v else "false") if isinstance(v, bool) else v
            # Measured on the platform (fy-s01, S01-08, 11 Oct): when a response lacks the path, the variable gets the value that was mapped
            # from an earlier response, even when code has overwritten it in between. A cleared optional field (`hint`) comes back.
            elif m["variable"] in self.mapped: self.vars[m["variable"]] = self.mapped[m["variable"]]
        return res


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m: raise ValueError("no JSON object")
    return json.loads(m.group(0))
