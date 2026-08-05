from config import LLM_PROVIDER
from llm.base import LLMProvider
from llm.ollama_provider import OllamaProvider


def get_llm_provider() -> LLMProvider:
    provider = LLM_PROVIDER.lower().strip()

    if provider == "ollama":
        return OllamaProvider()

    if provider == "openai":
        from llm.openai_provider import OpenAIProvider

        return OpenAIProvider()

    if provider == "anthropic":
        from llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    raise ValueError(
        f"Unsupported LLM provider: {LLM_PROVIDER}"
    )