"""One tiny chat completion against the Hostinger AI Router (costs a few dozen tokens).

Reads ROUTER_BASE_URL, ROUTER_API_KEY and MODEL_TAGGER from the environment.
  Server:  cd /docker/stoprop && docker compose exec stoprop python scripts/router_smoke.py
  PC:      set -a; source .env.local; set +a; .venv/Scripts/python scripts/router_smoke.py
"""
import json
import os
import sys
import time

from openai import OpenAI

REQUIRED = ("ROUTER_BASE_URL", "ROUTER_API_KEY", "MODEL_TAGGER")


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("Missing env vars (not set or empty): " + ", ".join(missing))
        return 2

    model = os.environ["MODEL_TAGGER"]
    client = OpenAI(
        base_url=os.environ["ROUTER_BASE_URL"],
        api_key=os.environ["ROUTER_API_KEY"],
        timeout=30,
        max_retries=0,
    )
    print(f"Calling {model} via {os.environ['ROUTER_BASE_URL']} ...")
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: Stoprop router OK"}],
            max_tokens=20,
            temperature=0,
        )
    except Exception as exc:  # print the error class + message, never the key
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.monotonic() - t0

    reply = resp.choices[0].message.content if resp.choices else None
    print(f"Reply ({elapsed:.1f}s, model={resp.model}): {reply!r}")
    usage = resp.usage.model_dump() if resp.usage else None
    print("Usage:", json.dumps(usage, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
