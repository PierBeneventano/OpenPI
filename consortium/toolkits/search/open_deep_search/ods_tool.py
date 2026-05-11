"""OpenDeepSearchTool — deep research via Perplexity (OpenRouter) or legacy Serper pipeline."""

from typing import Optional, Literal, Type
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, ConfigDict
import os
import litellm
from dotenv import load_dotenv

load_dotenv()


class OpenDeepSearchInput(BaseModel):
    query: str = Field(description="The search query to perform deep research on")


class OpenDeepSearchTool(BaseTool):
    """Web-augmented deep research tool.

    Primary mode: Perplexity deep research models via OpenRouter (no extra API key needed).
    Fallback mode: Legacy Serper + ODS agent pipeline (requires SERPER_API_KEY).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "deep_research"
    description: str = (
        "Performs deep web research on a topic using Perplexity's research models. "
        "Returns a comprehensive, citation-backed answer synthesized from multiple sources. "
        "Use for literature review, finding related work, verifying claims, or exploring "
        "the state of the art on a research topic."
    )
    args_schema: Type[BaseModel] = OpenDeepSearchInput

    model_name: Optional[str] = None
    perplexity_model: str = "openrouter/perplexity/sonar-deep-research"
    search_tool: Optional[object] = None  # Legacy ODS agent (if Serper available)
    _use_perplexity: bool = True

    def __init__(
        self,
        model_name: Optional[str] = None,
        perplexity_model: str = "openrouter/perplexity/sonar-deep-research",
        reranker: str = "infinity",
        search_provider: Literal["serper", "searxng"] = "serper",
        **kwargs,
    ):
        selected_perplexity_model = (
            os.getenv("OPENDEEPSEARCH_MODEL")
            or os.getenv("DEEP_RESEARCH_MODEL")
            or perplexity_model
        )
        super().__init__(
            model_name=model_name,
            perplexity_model=selected_perplexity_model,
            **kwargs,
        )
        self._use_perplexity = bool(os.getenv("OPENROUTER_API_KEY"))
        if not self._use_perplexity:
            self._setup_legacy(reranker, search_provider)

    def _setup_legacy(self, reranker: str, search_provider: str):
        """Fall back to the Serper + ODS agent pipeline."""
        serper_api_key = os.getenv("SERPER_API_KEY")
        searxng_instance_url = os.getenv("SEARXNG_INSTANCE_URL")
        if not serper_api_key and not searxng_instance_url:
            self.search_tool = None
            return
        try:
            from .ods_agent import OpenDeepSearchAgent
            self.search_tool = OpenDeepSearchAgent(
                model=self.model_name,
                reranker=reranker,
                search_provider=search_provider,
                serper_api_key=serper_api_key,
                searxng_instance_url=searxng_instance_url,
                searxng_api_key=os.getenv("SEARXNG_API_KEY"),
            )
        except Exception as e:
            print(f"[OpenDeepSearchTool] Legacy setup failed: {e}. Tool disabled.")
            self.search_tool = None

    def _run(self, query: str) -> str:
        if self._use_perplexity:
            return self._run_perplexity(query)
        if self.search_tool is not None:
            return self.search_tool.ask_sync(query, max_sources=2, pro_mode=True)
        return "[DeepResearch unavailable — no OPENROUTER_API_KEY or SERPER_API_KEY configured]"

    def _run_perplexity(self, query: str) -> str:
        """Use Perplexity deep research via OpenRouter."""
        try:
            resp = litellm.completion(
                model=self.perplexity_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a research assistant performing deep literature search. "
                            "Provide comprehensive, well-cited answers with specific paper titles, "
                            "authors, years, and key findings. Focus on the most relevant and "
                            "recent work. Include arXiv IDs where available."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                max_tokens=8192,
                timeout=3600,
            )
            answer = resp.choices[0].message.content or ""

            # Append citation URLs if available (Perplexity returns these)
            citations = []
            if hasattr(resp, "citations"):
                citations = resp.citations or []
            elif hasattr(resp, "_hidden_params"):
                hp = resp._hidden_params or {}
                citations = hp.get("citations", [])

            if citations:
                answer += "\n\n**Sources:**\n"
                for i, url in enumerate(citations[:10], 1):
                    answer += f"[{i}] {url}\n"

            return answer
        except Exception as e:
            # Fall back to a lighter Perplexity model
            try:
                fallback_model = (
                    os.getenv("OPENDEEPSEARCH_FALLBACK_MODEL")
                    or os.getenv("DEEP_RESEARCH_MODEL")
                    or "openrouter/perplexity/sonar-pro"
                )
                resp = litellm.completion(
                    model=fallback_model,
                    messages=[{"role": "user", "content": query}],
                    max_tokens=4096,
                    timeout=60,
                )
                return resp.choices[0].message.content or f"[Perplexity fallback — limited results for: {query}]"
            except Exception as e2:
                return f"[DeepResearch failed: {e}; fallback also failed: {e2}]"
