"""
llm_openai.py
-------------
OpenAI GPT integration with native async streaming.

Why GPT-4o-mini?
  - ~10x cheaper than GPT-4o
  - ~200ms TTFB (Time-To-First-Token) on short prompts
  - More than sufficient for 1-2 sentence conversational replies
  - Falls back to a warm dummy response if the key is missing (dev mode)
"""

import os
from openai import AsyncOpenAI
from dotenv import load_dotenv
from typing import AsyncGenerator

load_dotenv(override=True)

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def stream_response(messages: list[dict]) -> AsyncGenerator[str, None]:
    """
    Core streaming generator.  Yields individual text tokens as they arrive
    from OpenAI.  The caller (vapi_handler) wraps them in SSE format.

    This is what drops Time-To-First-Audio from ~2s to ~300ms:
    VAPI receives the first token and immediately starts synthesising speech
    while OpenAI is still generating the rest of the sentence.
    """
    if not os.getenv("OPENAI_API_KEY"):
        # Dev-mode warm response — no API key needed
        fallback = "Hi! This is a demo reply. Set OPENAI_API_KEY for live responses."
        for word in fallback.split():
            yield word + " "
        return

    try:
        stream = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=120,          # Keep phone responses SHORT
            temperature=0.75,        # Natural variability, not robotic
            presence_penalty=0.1,    # Discourage repetition
            stream=True
        )
        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield token
    except Exception as e:
        print(f"[LLM] OpenAI streaming error: {e}")
        yield "I'm sorry, could you repeat that?"


async def get_response(messages: list[dict]) -> str:
    """
    Non-streaming convenience wrapper.
    Used for pre-generating greeting audio before the call connects.
    """
    if not os.getenv("OPENAI_API_KEY"):
        return "Hello from Riverwood! This is a demo."

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM] OpenAI non-streaming error: {e}")
        return "Hello! I'll connect you with the latest updates on your property."
