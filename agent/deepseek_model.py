"""mini-swe-agent's tool-call model, sent to DeepSeek's OpenAI-compatible API, with cost counted at peak prices.

litellm 1.106.6 imports its proxy's MCP code on every call with tools, which needs a package we did not install, so
the call goes through the openai SDK that mini-swe-agent already depends on.

Every request passes one gate right before the SDK call: the complete request (messages with tool output, tools and
every setting, keys included) is checked for private text, the grid's stop flag is checked, and its worst-case cost
is reserved in the ledger first. A request that fails the gate is not sent and not retried.
"""

import json
import os
import threading

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # litellm must not fetch its price map at import

import openai  # noqa: E402
from minisweagent.models.litellm_model import LitellmModel  # noqa: E402
from minisweagent.models.utils.actions_toolcall import BASH_TOOL  # noqa: E402

from agent.pricing import MISS, OUTPUT, cost  # noqa: E402
from replay.ledger import BudgetExceeded, Halted, LedgerInvalid, MissingUsage  # noqa: E402
from replay.redact import request_problems  # noqa: E402

BASE_URL = "https://api.deepseek.com"


class PrivateTextBlocked(Exception):
    """A request would have carried private text, so it was not sent."""


def worst_case(request: dict) -> float:
    """Every byte of the request a token (byte-level BPE never needs more), none cached, the full output allowance."""
    return len(json.dumps(request).encode()) * MISS + request["max_tokens"] * OUTPUT


class DeepSeekModel(LitellmModel):
    abort_exceptions = [
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
        openai.BadRequestError,
        ImportError,
        KeyboardInterrupt,
        PrivateTextBlocked,
        BudgetExceeded,
        LedgerInvalid,
        MissingUsage,
        Halted,
    ]

    def __init__(self, ledger, stop: threading.Event | None = None, **kwargs):
        super().__init__(**kwargs)
        if ledger is None:
            raise LedgerInvalid("every paid request needs the ledger")
        self.ledger, self.stop = ledger, stop or threading.Event()
        # No hidden SDK retries: mini-swe-agent's own retries are the only ones, and they are logged.
        self.client = openai.OpenAI(
            base_url=BASE_URL, api_key=os.environ["DEEPSEEK_API_KEY"], timeout=600, max_retries=0
        )

    def _query(self, messages: list[dict], **kwargs):
        request = {"model": self.config.model_name, "messages": messages, "tools": [BASH_TOOL]}
        request |= self.config.model_kwargs | kwargs
        if found := request_problems(request):
            self.fail(f"privacy block: {found[:5]}")
            raise PrivateTextBlocked(f"request not sent, it holds {found[:5]}")
        send = lambda: self.client.chat.completions.create(**request)  # noqa: E731
        return self.ledger.dispatch(worst_case(request), send, cost, self.stop)

    def fail(self, reason: str) -> None:
        """Latch a gate failure at once: this process's stop flag and the ledger's stop file for every process."""
        self.stop.set()
        self.ledger.halt(reason)

    def _calculate_cost(self, response) -> dict[str, float]:
        return {"cost": cost(response.usage)}
