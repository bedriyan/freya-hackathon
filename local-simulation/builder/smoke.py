"""Send one customer line to a saved workflow through the chat route and print what the phone agent says first.

    python3 smoke.py <workflow_id>

A reply means the workflow starts. HTTP 500 means it cannot start a call at all. 503/504 means the platform is busy: wait and repeat.
"""
import json, sys, time, urllib.request, urllib.error
import freya

body = {"workflowId": sys.argv[1], "message": "Hello?", "customerVariables": {"customer_id": "c_smoke", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}}
for attempt in range(4):
    req = urllib.request.Request(freya.URL + "/api/v2/chat/message", data=json.dumps(body).encode(), headers=freya.HEADERS, method="POST")
    try:
        r = json.load(urllib.request.urlopen(req, timeout=90))["data"]["result"]
        print("agent says:", r.get("response"))
        urllib.request.urlopen(urllib.request.Request(freya.URL + "/api/v2/chat/end", data=json.dumps({"callId": r["callId"]}).encode(), headers=freya.HEADERS, method="POST"), timeout=60)
        sys.exit(0)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300])
        if e.code == 500: sys.exit(1)
        time.sleep(20)
sys.exit(2)
