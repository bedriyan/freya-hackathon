"""run_cases.py with network retries. Same arguments as kit/build-the-builder/tools/run_cases.py.

The kit's client raises on the first dropped connection, which loses a whole simulation run.
"""
import pathlib, sys, time, urllib.error

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit" / "build-the-builder"
sys.path.insert(0, str(KIT)); sys.path.insert(0, str(KIT / "tools"))
import freya, score


def retrying(fn, tries=40, wait=15):
    def wrapped(*a, **k):
        for i in range(tries):
            try:
                return fn(*a, **k)
            except RuntimeError as e:                    # the Freya client turns an HTTP error into a RuntimeError; a 5xx of the platform's gateway passes
                if not any(f"-> {c}:" in str(e) for c in (500, 502, 503, 504)) or a[:1] != ("GET",) or i == tries - 1:
                    raise
                print(f"platform error ({str(e)[:80]}), retry {i + 1} in {wait} s", flush=True); time.sleep(wait)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if isinstance(e, urllib.error.HTTPError) or i == tries - 1:
                    raise
                print(f"network error ({e}), retry {i + 1} in {wait} s", flush=True); time.sleep(wait)
    return wrapped


_api = freya.api
def api(method, path, body=None, **params):
    # a POST that creates something is not repeated blindly: only reads and idempotent calls are retried
    if method == "POST" and path in ("/api/v2/simulations", "/api/v2/personas", "/api/v2/workflow"):
        return _api(method, path, body, **params)
    return retrying(_api)(method, path, body, **params)
freya.api = api
score.http = retrying(score.http)

import run_cases
run_cases.http = score.http
if __name__ == "__main__":
    run_cases.main()
