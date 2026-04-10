"""
llm.py — Provider-agnostic LLM factory for git-crew

All three frameworks (LangGraph/CrewAI via LangChain, AutoGen) pull their
LLM configuration from this single module. Switching providers requires only
changing environment variables — no code changes needed.

Configure via environment variables
────────────────────────────────────
  LLM_PROVIDER   Which provider to use (default: groq)
  LLM_MODEL      Override the provider's default model (optional)

Provider reference
──────────────────
  Provider      Key env var(s)                          Default model
  ─────────────────────────────────────────────────────────────────────────────
  groq          GROQ_API_KEY                            llama-3.3-70b-versatile
  openai        OPENAI_API_KEY                          gpt-4o
  anthropic     ANTHROPIC_API_KEY                       claude-3-5-sonnet-20241022
  ollama        OLLAMA_BASE_URL (opt, default localhost) llama3.3
  azure         AZURE_OPENAI_API_KEY                    gpt-4o
                AZURE_OPENAI_ENDPOINT
                AZURE_OPENAI_API_VERSION (opt)
  mistral       MISTRAL_API_KEY                         mistral-large-latest
  google        GOOGLE_API_KEY                          gemini-2.0-flash
  openrouter    OPENROUTER_API_KEY                      openai/gpt-4o
  together      TOGETHER_API_KEY                        meta-llama/Llama-3-70b-chat-hf

Install the matching LangChain package for your provider:
  pip install langchain-groq          # groq (included by default)
  pip install langchain-openai        # openai, azure, openrouter, together
  pip install langchain-anthropic     # anthropic
  pip install langchain-ollama        # ollama
  pip install langchain-mistralai     # mistral
  pip install langchain-google-genai  # google
"""

import os

# ── Provider metadata ──────────────────────────────────────────────────────────
# Maps provider name → (default_model, required_api_key_env_var | None)

_PROVIDERS: dict[str, tuple[str, str | None]] = {
    "groq":       ("llama-3.3-70b-versatile",        "GROQ_API_KEY"),
    "openai":     ("gpt-4o",                         "OPENAI_API_KEY"),
    "anthropic":  ("claude-3-5-sonnet-20241022",     "ANTHROPIC_API_KEY"),
    "ollama":     ("llama3.3",                       None),
    "azure":      ("gpt-4o",                         "AZURE_OPENAI_API_KEY"),
    "mistral":    ("mistral-large-latest",           "MISTRAL_API_KEY"),
    "google":     ("gemini-2.0-flash",               "GOOGLE_API_KEY"),
    "openrouter": ("openai/gpt-4o",                  "OPENROUTER_API_KEY"),
    "together":   ("meta-llama/Llama-3-70b-chat-hf", "TOGETHER_API_KEY"),
}


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "groq").lower().strip()


def _model(provider: str) -> str:
    default, _ = _PROVIDERS.get(provider, ("gpt-4o", None))
    return os.environ.get("LLM_MODEL", default)


def _require_key(env_var: str, provider: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise EnvironmentError(
            f"{env_var} is not set.\n"
            f"  LLM_PROVIDER={provider} requires {env_var}.\n"
            f"  Add it to your .env file or export it before running."
        )
    return value


# ── LangChain factory ──────────────────────────────────────────────────────────
# Used by: CrewAI crew agents (agent.llm parameter)

def get_langchain_llm(temperature: float = 0.1):
    """
    Return a LangChain BaseChatModel for the active LLM_PROVIDER.

    The returned object is compatible with CrewAI's agent.llm parameter
    and can also be used directly with LangChain chains.

    Raises:
        EnvironmentError: if the required API key is not set
        ValueError: if LLM_PROVIDER is not a recognised provider
        ImportError: if the provider's langchain package is not installed
    """
    p = _provider()
    m = _model(p)

    if p == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=m, temperature=temperature,
                        api_key=_require_key("GROQ_API_KEY", p))

    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=m, temperature=temperature,
                          api_key=_require_key("OPENAI_API_KEY", p))

    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=m, temperature=temperature,
                             api_key=_require_key("ANTHROPIC_API_KEY", p))

    if p == "ollama":
        from langchain_ollama import ChatOllama
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=m, temperature=temperature, base_url=base_url)

    if p == "azure":
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_deployment=m,
            temperature=temperature,
            api_key=_require_key("AZURE_OPENAI_API_KEY", p),
            azure_endpoint=_require_key("AZURE_OPENAI_ENDPOINT", p),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )

    if p == "mistral":
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(model=m, temperature=temperature,
                             api_key=_require_key("MISTRAL_API_KEY", p))

    if p == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=m, temperature=temperature,
            google_api_key=_require_key("GOOGLE_API_KEY", p),
        )

    if p in ("openrouter", "together"):
        from langchain_openai import ChatOpenAI
        endpoints = {
            "openrouter": "https://openrouter.ai/api/v1",
            "together":   "https://api.together.xyz/v1",
        }
        key_vars = {
            "openrouter": "OPENROUTER_API_KEY",
            "together":   "TOGETHER_API_KEY",
        }
        return ChatOpenAI(
            model=m,
            temperature=temperature,
            api_key=_require_key(key_vars[p], p),
            base_url=endpoints[p],
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {p!r}.\n"
        f"Supported providers: {', '.join(_PROVIDERS)}\n"
        f"Set LLM_PROVIDER in your .env file."
    )


# ── AutoGen config factory ─────────────────────────────────────────────────────
# Used by: interactive.py (post-review Q&A session)

def get_autogen_config() -> dict:
    """
    Return an AutoGen llm_config dict for the active LLM_PROVIDER.

    AutoGen speaks OpenAI's protocol natively. For non-OpenAI providers
    we use their OpenAI-compatible endpoints where available, or their
    native api_type where AutoGen supports it (e.g. anthropic, azure).

    Raises:
        EnvironmentError: if the required API key is not set
        ValueError: if LLM_PROVIDER is not a recognised provider
    """
    p = _provider()
    m = _model(p)

    def _cfg(**kwargs) -> dict:
        return {"config_list": [{"model": m, "api_type": "openai", **kwargs}],
                "temperature": 0.2}

    if p == "groq":
        return _cfg(api_key=_require_key("GROQ_API_KEY", p),
                    base_url="https://api.groq.com/openai/v1")

    if p == "openai":
        return _cfg(api_key=_require_key("OPENAI_API_KEY", p))

    if p == "anthropic":
        return {"config_list": [{"model": m, "api_type": "anthropic",
                                  "api_key": _require_key("ANTHROPIC_API_KEY", p)}],
                "temperature": 0.2}

    if p == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return _cfg(api_key="ollama", base_url=f"{base_url}/v1")

    if p == "azure":
        return {"config_list": [{
            "model": m,
            "api_type": "azure",
            "api_key": _require_key("AZURE_OPENAI_API_KEY", p),
            "base_url": _require_key("AZURE_OPENAI_ENDPOINT", p),
            "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        }], "temperature": 0.2}

    if p == "mistral":
        return _cfg(api_key=_require_key("MISTRAL_API_KEY", p),
                    base_url="https://api.mistral.ai/v1")

    if p == "google":
        # Google's OpenAI-compatible endpoint
        return _cfg(api_key=_require_key("GOOGLE_API_KEY", p),
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/")

    if p == "openrouter":
        return _cfg(api_key=_require_key("OPENROUTER_API_KEY", p),
                    base_url="https://openrouter.ai/api/v1")

    if p == "together":
        return _cfg(api_key=_require_key("TOGETHER_API_KEY", p),
                    base_url="https://api.together.xyz/v1")

    raise ValueError(
        f"Unknown LLM_PROVIDER: {p!r}.\n"
        f"Supported providers: {', '.join(_PROVIDERS)}"
    )


# ── Introspection ──────────────────────────────────────────────────────────────

def describe_active() -> str:
    """Human-readable summary of the active LLM (shown in CLI header)."""
    p = _provider()
    m = _model(p)
    return f"{p} / {m}"
