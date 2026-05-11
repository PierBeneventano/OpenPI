"""
DeepResearchNoveltyScanTool — adversarial novelty checker for mathematical claims.

Uses deep-research APIs (Perplexity sonar-deep-research or OpenAI reasoning models)
to search across academic papers, MathOverflow, theses, and web sources for prior
proofs of specific mathematical claims.

All API calls go through litellm.completion() for budget tracking consistency.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional, Type

import litellm
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


class DeepResearchNoveltyScanInput(BaseModel):
    claim_text: str = Field(
        description=(
            "The precise mathematical claim, theorem, or conjecture to check. "
            "Include the full statement with conditions and conclusions."
        )
    )
    context: str = Field(
        default="",
        description=(
            "Optional context about the research area, related work, or "
            "terminology that may help locate equivalent results."
        ),
    )


_NOVELTY_SYSTEM_PROMPT = """\
You are an adversarial novelty checker for mathematical research claims.
Your job is to find evidence that the given claim is NOT novel — that it has
already been proven, established, or is equivalent to a known result.

Search specifically for:
1. Direct proofs of the claim in published papers, theses, or technical reports.
2. Known special cases or partial results that subsume the claim.
3. Equivalent formulations under different terminology or in adjacent fields
   (e.g., the same theorem in statistics vs. ML, or in functional analysis vs.
   optimization theory).
4. Discussions on MathOverflow, zbMATH, nLab, or Wikipedia that reference the
   claim as established.

Be thorough and honest. If the claim appears genuinely novel after exhaustive
search, say so. But your default stance is skepticism — assume the claim is
known until proven otherwise.

Respond with a structured analysis:
- VERDICT: OPEN | PARTIAL | KNOWN | EQUIVALENT_KNOWN
- CONFIDENCE: high | medium | low
- EVIDENCE: List each relevant source with title, URL, and how it relates.
- SUMMARY: 1-2 paragraph explanation of your finding.
"""


class DeepResearchNoveltyScanTool(BaseTool):
    """Check whether a mathematical claim has been previously proven."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "DeepResearchNoveltyScanTool"
    description: str = (
        "Use this tool to check whether a specific mathematical claim, theorem, "
        "or conjecture has been previously proven in the literature. Input should "
        "be the precise claim text. This tool searches across academic papers, "
        "MathOverflow, theses, and web sources to find prior proofs or equivalent "
        "results. Returns a structured finding with verdict and sources."
    )
    args_schema: Type[BaseModel] = DeepResearchNoveltyScanInput

    model_name: Optional[str] = None

    def __init__(self, model_name: Optional[str] = None, **kwargs: Any):
        super().__init__(model_name=model_name, **kwargs)

    def _run(self, claim_text: str, context: str = "") -> str:
        """Search for prior proofs of the given claim.

        Returns a JSON string with keys:
            claim_text, finding, sources, verdict, confidence
        """
        backend = os.getenv("DEEP_RESEARCH_BACKEND", "perplexity").lower()

        if self.model_name:
            # Explicit model override takes precedence over env var defaults
            model_id = self.model_name
        elif backend == "perplexity":
            model_id = os.getenv(
                "DEEP_RESEARCH_MODEL",
                "openrouter/perplexity/sonar-deep-research",
            )
        else:
            model_id = os.getenv("DEEP_RESEARCH_OPENAI_MODEL", "o3")

        user_prompt = f"CLAIM TO CHECK:\n{claim_text}"
        if context:
            user_prompt += f"\n\nCONTEXT:\n{context}"

        try:
            max_tokens = int(os.getenv("DEEP_RESEARCH_MAX_TOKENS", "4096"))
            resp = litellm.completion(
                model=model_id,
                messages=[
                    {"role": "system", "content": _NOVELTY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
            finding = resp.choices[0].message.content or ""
        except Exception as e:
            # Graceful degradation — never break the agent
            print(
                f"[DeepResearchNoveltyScanTool] API call failed ({e}), "
                f"returning UNCERTAIN."
            )
            return json.dumps({
                "claim_text": claim_text,
                "finding": "[deep research unavailable]",
                "sources": [],
                "verdict": "UNCERTAIN",
                "confidence": "low",
            })

        # Extract structured fields from the response
        verdict = self._extract_verdict(finding)
        confidence = self._extract_confidence(finding)
        sources = self._extract_sources(finding)

        return json.dumps({
            "claim_text": claim_text,
            "finding": finding,
            "sources": sources,
            "verdict": verdict,
            "confidence": confidence,
        })

    @staticmethod
    def _extract_verdict(text: str) -> str:
        """Extract verdict from the deep research response."""
        upper = text.upper()
        match = re.search(
            r"VERDICT\s*:\s*(OPEN|PARTIAL|KNOWN|EQUIVALENT_KNOWN)", upper
        )
        if match:
            return match.group(1)
        # Heuristic fallback
        for phrase, verdict in [
            ("HAS BEEN PROVEN", "KNOWN"),
            ("IS A KNOWN RESULT", "KNOWN"),
            ("WAS ESTABLISHED BY", "KNOWN"),
            ("ALREADY PROVEN", "KNOWN"),
            ("IS EQUIVALENT TO", "EQUIVALENT_KNOWN"),
            ("EQUIVALENT RESULT", "EQUIVALENT_KNOWN"),
            ("PARTIAL RESULT", "PARTIAL"),
            ("SPECIAL CASE", "PARTIAL"),
            ("APPEARS NOVEL", "OPEN"),
            ("NO EVIDENCE", "OPEN"),
            ("GENUINELY NOVEL", "OPEN"),
        ]:
            if phrase in upper:
                return verdict
        return "UNCERTAIN"

    @staticmethod
    def _extract_confidence(text: str) -> str:
        """Extract confidence level from response."""
        upper = text.upper()
        match = re.search(r"CONFIDENCE\s*:\s*(HIGH|MEDIUM|LOW)", upper)
        if match:
            return match.group(1).lower()
        return "medium"

    @staticmethod
    def _extract_sources(text: str) -> list:
        """Extract URLs from the response as source list."""
        urls = re.findall(r"https?://[^\s\)\"'>]+", text)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique
