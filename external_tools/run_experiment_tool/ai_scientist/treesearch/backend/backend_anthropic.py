"""Anthropic/Claude model backend — routed through OpenRouter (OpenAI-compatible API).

All Claude tree search calls use the OpenAI client pointed at OpenRouter,
so we share the same message/tool format as backend_openai.py.
"""

import json
import logging
import os
import time

from .utils import FunctionSpec, OutputType, opt_messages_to_list, backoff_create
from funcy import notnone, once, select_values
import openai

logger = logging.getLogger("ai-scientist")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_client: openai.OpenAI = None  # type: ignore

TIMEOUT_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    openai.APIStatusError,
)


@once
def _setup_client():
    global _client
    _client = openai.OpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=OPENROUTER_BASE_URL,
        max_retries=0,
    )


def _normalize_tool_spec(func_spec: FunctionSpec | dict) -> tuple[str, dict, dict]:
    """Convert a FunctionSpec or dict to OpenAI tool format."""
    if isinstance(func_spec, FunctionSpec):
        return (
            func_spec.name,
            func_spec.as_openai_tool_dict,
            func_spec.openai_tool_choice_dict,
        )

    if isinstance(func_spec, dict):
        tool_name = func_spec.get("name")
        tool_desc = func_spec.get("description", "")
        parameters = func_spec.get("json_schema", func_spec.get("parameters", {}))
        if not tool_name:
            raise ValueError(f"Invalid tool spec, missing name: {func_spec}")
        return (
            tool_name,
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_desc,
                    "parameters": parameters,
                },
            },
            {
                "type": "function",
                "function": {"name": tool_name},
            },
        )

    raise TypeError(f"Unsupported func_spec type: {type(func_spec)}")


def query(
    system_message: str | None,
    user_message: str | list | None,
    func_spec: FunctionSpec | dict | None = None,
    **model_kwargs,
) -> tuple[OutputType, float, int, int, dict]:
    _setup_client()

    filtered_kwargs: dict = select_values(notnone, model_kwargs)  # type: ignore
    if "max_tokens" not in filtered_kwargs:
        filtered_kwargs["max_tokens"] = 8192

    tool_name = None
    if func_spec is not None:
        tool_name, tool_spec, tool_choice = _normalize_tool_spec(func_spec)
        filtered_kwargs["tools"] = [tool_spec]
        filtered_kwargs["tool_choice"] = tool_choice

    messages = opt_messages_to_list(system_message, user_message)

    t0 = time.time()
    completion = backoff_create(
        _client.chat.completions.create,
        TIMEOUT_EXCEPTIONS,
        messages=messages,
        **filtered_kwargs,
    )
    req_time = time.time() - t0

    choice = completion.choices[0]

    if func_spec is not None:
        if not choice.message.tool_calls:
            raise ValueError(f"Model returned no tool call for tool {tool_name}.")
        selected_call = next(
            (tc for tc in choice.message.tool_calls if tc.function.name == tool_name),
            choice.message.tool_calls[0],
        )
        try:
            output: OutputType = json.loads(selected_call.function.arguments)
        except json.JSONDecodeError as e:
            logger.error("Error decoding tool arguments: %s", selected_call.function.arguments)
            raise e
    else:
        output = choice.message.content
        if not output:
            raise ValueError("Model returned no text content.")
        output = output.strip()

    in_tokens = completion.usage.prompt_tokens
    out_tokens = completion.usage.completion_tokens

    info = {
        "stop_reason": choice.finish_reason,
    }

    return output, req_time, in_tokens, out_tokens, info
