import json
import urllib.error
import urllib.request
#import subprocess
from typing import Any
import logging

from config import (
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    OLLAMA_HOST,
    OPENAI_API_KEY,
)


class LLMError(RuntimeError):
    """Raised when an LLM provider cannot return a usable response."""

def _escape_control_chars_in_json_strings(text: str) -> str:
    """
    Escape raw newline, carriage-return, and tab characters
    that appear inside quoted JSON strings.

    LLMs occasionally return visually formatted JSON containing
    literal line breaks inside string values, which is invalid JSON.
    """

    output = []

    inside_string = False
    escaped = False

    for char in text:

        if inside_string:

            if escaped:
                output.append(char)
                escaped = False
                continue

            if char == "\\":
                output.append(char)
                escaped = True
                continue

            if char == '"':
                output.append(char)
                inside_string = False
                continue

            if char == "\n":
                output.append("\\n")
                continue

            if char == "\r":
                output.append("\\r")
                continue

            if char == "\t":
                output.append("\\t")
                continue

            output.append(char)

        else:

            output.append(char)

            if char == '"':
                inside_string = True

    return "".join(output)

def _extract_json_object(response_text: str) -> dict:
    """
    Extract and parse a JSON object from an LLM response.

    Handles:
    - pure JSON
    - Markdown code fences
    - explanatory prose before/after JSON
    - raw control characters inside JSON strings
    """

    cleaned = response_text.strip()

    # ---------------------------------------------------------
    # Remove Markdown code fences
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
    # First try parsing the entire response.
    #
    # strict=False allows literal control characters such as
    # newlines and tabs inside LLM-generated string values.
    # ---------------------------------------------------------

    try:

        parsed = json.loads(
            cleaned,
            strict=False,
        )

        if not isinstance(
            parsed,
            dict,
        ):
            raise LLMError(
                "The LLM response must be a JSON object."
            )

        return parsed

    except json.JSONDecodeError:
        pass

    # ---------------------------------------------------------
    # Model may have included prose before or after the JSON.
    # Locate the first JSON object.
    # ---------------------------------------------------------

    start = cleaned.find("{")

    if start == -1:

        raise LLMError(
            "The LLM response did not contain a JSON object.\n\n"
            f"FULL RESPONSE:\n{cleaned}"
        )

    decoder = json.JSONDecoder(
        strict=False
    )

    try:

        parsed, end_position = decoder.raw_decode(
            cleaned[start:]
        )
        
    except json.JSONDecodeError as exc:

        error_start = max(
            0,
            exc.pos - 50
        )

        error_end = min(
            len(cleaned),
            exc.pos + 50
        )

        error_context = cleaned[
            error_start:error_end
        ]

        raise LLMError(
            "The LLM returned invalid JSON.\n\n"
            f"JSON ERROR: {exc}\n\n"
            f"ERROR POSITION: {exc.pos}\n\n"
            f"ERROR CONTEXT REPR:\n"
            f"{repr(error_context)}\n\n"
            f"FULL RESPONSE:\n{cleaned}"
        ) from exc

    # except json.JSONDecodeError as exc:

        # raise LLMError(
            # "The LLM returned invalid JSON.\n\n"
            # f"JSON ERROR: {exc}\n\n"
            # f"FULL RESPONSE:\n{cleaned}"
        # ) from exc

    # if not isinstance(
        # parsed,
        # dict,
    # ):

        # raise LLMError(
            # "The extracted LLM response must be a JSON object."
        # )

    return parsed


def _call_ollama(prompt: str) -> str:
    """
    Call Ollama through its local HTTP API.

    Using the API instead of `ollama run` prevents terminal
    control sequences from contaminating machine-readable output.
    """

    url = (
        OLLAMA_HOST.rstrip("/")
        + "/api/generate"
    )

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,

        # Tell Ollama we expect JSON output.
        "format": "json",

        "options": {
            "temperature": 0.1,
        },
    }

    request = urllib.request.Request(
        url=url,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=LLM_TIMEOUT_SECONDS,
        ) as response:

            response_body = (
                response
                .read()
                .decode("utf-8")
            )

    except TimeoutError as exc:

        raise LLMError(
            f"Ollama timed out after "
            f"{LLM_TIMEOUT_SECONDS} seconds."
        ) from exc

    except urllib.error.URLError as exc:

        raise LLMError(
            f"Ollama API request failed: {exc}"
        ) from exc

    # ---------------------------------------------------------
    # Parse the Ollama API envelope
    # ---------------------------------------------------------

    try:

        api_response = json.loads(
            response_body
        )

    except json.JSONDecodeError as exc:

        raise LLMError(
            "Ollama returned an invalid API response."
        ) from exc

    response_text = (
        api_response
        .get(
            "response",
            ""
        )
        .strip()
    )

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