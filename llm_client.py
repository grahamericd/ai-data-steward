import json
import subprocess
from typing import Any
import logging

from config import (
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
)


class LLMError(RuntimeError):
    """Raised when an LLM provider cannot return a usable response."""


def _extract_json_object(response_text: str) -> dict:
    """
    Extract and parse a JSON object from an LLM response.

    Handles:
    - pure JSON responses
    - Markdown code fences
    - explanatory text before or after the JSON
    """

    cleaned = response_text.strip()

    # ---------------------------------------------------------
    # Remove Markdown code fences when present
    # ---------------------------------------------------------

    if cleaned.startswith("```"):

        lines = cleaned.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(
            lines
        ).strip()

        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    # ---------------------------------------------------------
    # First try normal JSON parsing
    # ---------------------------------------------------------

    try:

        parsed = json.loads(
            cleaned
        )

        if not isinstance(
            parsed,
            dict
        ):
            raise LLMError(
                "The LLM response must be a JSON object."
            )

        return parsed

    except json.JSONDecodeError:
        pass

    # ---------------------------------------------------------
    # Model may have included prose before/after JSON.
    # Find the first complete JSON object.
    # ---------------------------------------------------------

    start = cleaned.find("{")

    if start == -1:

        raise LLMError(
            "The LLM response did not contain a JSON object. "
            f"Response begins with: {cleaned[:500]}"
        )

    decoder = json.JSONDecoder()

    try:

        parsed, end_position = decoder.raw_decode(
            cleaned[start:]
        )

    except json.JSONDecodeError as exc:
        
        raise LLMError(
            "The LLM returned invalid JSON.\n\n"
            f"JSON ERROR: {exc}\n\n"
            "FULL RESPONSE:\n"
            f"{cleaned}"
        ) from exc

        # raise LLMError(
            # "The LLM returned invalid JSON. "
            # f"Response begins with: {cleaned[:500]}"
        # ) from exc

    if not isinstance(
        parsed,
        dict
    ):

        raise LLMError(
            "The extracted LLM response must be a JSON object."
        )

    return parsed


def _call_ollama(prompt: str) -> str:
    """Call a locally installed Ollama model."""

    try:
        result = subprocess.run(
            [
                "ollama",
                "run",
                LLM_MODEL,
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_SECONDS,
            check=False,
        )

    except subprocess.TimeoutExpired as exc:
        raise LLMError(
            f"Ollama timed out after "
            f"{LLM_TIMEOUT_SECONDS} seconds."
        ) from exc

    except FileNotFoundError as exc:
        raise LLMError(
            "The Ollama executable was not found."
        ) from exc

    if result.returncode != 0:
        raise LLMError(
            "Ollama request failed: "
            f"{result.stderr.strip()}"
        )

    response_text = result.stdout.strip()

    if not response_text:
        raise LLMError(
            "Ollama returned an empty response."
        )

    return response_text


def _call_openai(prompt: str) -> str:
    """Call the OpenAI Responses API."""

    logger.warning(
        "Using an external LLM provider. Prompt content will be sent "
        "outside the local AI Data Steward environment."
    )

    if not OPENAI_API_KEY:
        raise LLMError(
            "OPENAI_API_KEY is required when "
            "LLM_PROVIDER=openai."
        )

    try:
        from openai import OpenAI

    except ImportError as exc:
        raise LLMError(
            "The OpenAI package is not installed. "
            "Run: pip install openai"
        ) from exc

    client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=LLM_TIMEOUT_SECONDS,
    )

    try:
        response = client.responses.create(
            model=LLM_MODEL,
            instructions=(
                "You are a senior enterprise data steward. "
                "Follow the user's requested JSON schema exactly. "
                "Return only valid JSON."
            ),
            input=prompt,
        )

    except Exception as exc:
        raise LLMError(
            f"OpenAI request failed: {exc}"
        ) from exc

    response_text = response.output_text.strip()

    if not response_text:
        raise LLMError(
            "OpenAI returned an empty response."
        )

    return response_text


def generate_text(prompt: str) -> str:
    """
    Generate text using the configured LLM provider.
    """

    if LLM_PROVIDER == "ollama":
        return _call_ollama(prompt)

    if LLM_PROVIDER == "openai":
        return _call_openai(prompt)

    raise LLMError(
        f"Unsupported LLM provider: {LLM_PROVIDER}"
    )


def generate_json(prompt: str) -> dict[str, Any]:
    """
    Generate and parse a JSON response.
    """

    response_text = generate_text(prompt)

    return _extract_json_object(
        response_text
    )