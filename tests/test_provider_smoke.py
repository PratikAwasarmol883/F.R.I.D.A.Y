import asyncio
import os

import pytest

from friday.adapters.llm import ChatMessage
from friday.adapters.llm.anthropic_provider import AnthropicProvider
from friday.core.config import settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set (opt-in smoke test)"
)


def test_real_provider_round_trip() -> None:
    provider = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], settings.llm_model)
    result = asyncio.run(
        provider.complete(
            system="Reply with one short word.",
            messages=[ChatMessage("user", "Say hello.")],
            max_output_tokens=20,
            timeout_seconds=30,
        )
    )
    assert result.text.strip()
    assert result.input_tokens > 0 and result.output_tokens > 0
