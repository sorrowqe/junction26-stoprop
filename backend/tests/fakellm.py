"""A fake LLM for tests: answers from a responder function, records calls, logs usage like the real one."""
import asyncio
import re

from app.llm import client as llm_client
from app.llm.client import LLMResult
from app.store import get_store


def sentences_in(user_msg: str) -> dict[str, str]:
    return dict(re.findall(r"^\[(p\d+s\d+)\] (.*)$", user_msg, re.MULTILINE))


class FakeLLM:
    def __init__(self, responder=None, tokens=(100, 20), delay=0.0, fail=False):
        self.responder = responder or (lambda user, sents: "[]")
        self.tokens = tokens
        self.delay = delay
        self.fail = fail
        self.calls: list[list[dict]] = []

    async def __call__(self, messages, *, model, max_tokens=2000, kind="tagger"):
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            get_store().log_usage(kind, model, 0, 0, 1, ok=False, error="APITimeoutError")
            raise llm_client.LLMError("APITimeoutError")
        user = messages[-1]["content"]
        text = self.responder(user, sentences_in(user))
        get_store().log_usage(kind, model, self.tokens[0], self.tokens[1], 5, ok=True)
        return LLMResult(text=text, input_tokens=self.tokens[0], output_tokens=self.tokens[1], latency_ms=5, model=model)

    @property
    def user_messages(self) -> list[str]:
        return [m[-1]["content"] for m in self.calls]


def install(monkeypatch, fake: FakeLLM) -> FakeLLM:
    monkeypatch.setenv("ROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(llm_client, "chat", fake)
    return fake
