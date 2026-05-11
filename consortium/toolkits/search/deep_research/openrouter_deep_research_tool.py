"""
OpenRouterDeepResearchTool — comprehensive academic literature search via OpenRouter.

Uses deep-research models (Perplexity sonar-deep-research by default) through
OpenRouter to search across arXiv, Semantic Scholar, Google Scholar, and other
academic databases simultaneously.

All API calls go through litellm.completion() for budget tracking consistency.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Type

import litellm
import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from ..provider_rate_limit import (
    ProviderRateGate,
    ProviderRateLimitTimeout,
    parse_retry_after_seconds,
)

# ---------------------------------------------------------------------------
# Module-level LRU response cache for deep research queries.
# Keyed by (query, max_papers, focus_area) hash. TTL = 30 min.
# ---------------------------------------------------------------------------
_RESPONSE_CACHE: Dict[str, tuple[float, str]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX_ENTRIES = 256


def _cache_key(query: str, max_papers: int, focus_area: str) -> str:
    raw = f"{query.strip().lower()}|{max_papers}|{focus_area.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    with _CACHE_LOCK:
        entry = _RESPONSE_CACHE.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts > _CACHE_TTL:
            del _RESPONSE_CACHE[key]
            return None
        return val


def _cache_put(key: str, value: str) -> None:
    with _CACHE_LOCK:
        if len(_RESPONSE_CACHE) >= _CACHE_MAX_ENTRIES:
            oldest_key = min(_RESPONSE_CACHE, key=lambda k: _RESPONSE_CACHE[k][0])
            del _RESPONSE_CACHE[oldest_key]
        _RESPONSE_CACHE[key] = (time.time(), value)


def clear_response_cache() -> None:
    """Clear the module-level response cache (useful for tests)."""
    with _CACHE_LOCK:
        _RESPONSE_CACHE.clear()


class OpenRouterDeepResearchInput(BaseModel):
    query: str = Field(
        description=(
            "Research query or topic to investigate. Can be a research question, "
            "paper title, method name, or broad topic. The tool searches across "
            "arXiv, Semantic Scholar, Google Scholar, and other academic databases."
        )
    )
    max_papers: int = Field(
        default=15,
        description="Target number of papers to find (default: 15).",
    )
    focus_area: str = Field(
        default="",
        description=(
            "Optional constraint to narrow the search: specific subfield, "
            "venue filter, time range, or methodology focus."
        ),
    )


_DEEP_RESEARCH_SYSTEM_PROMPT = """\
You are an expert academic literature search assistant. Your job is to find
real, published academic papers relevant to the given research query.

CRITICAL RULES:
1. Only cite papers that ACTUALLY EXIST. Never invent titles, authors, or venues.
2. For EVERY paper, include its arXiv ID (e.g., arXiv:2301.12345) if it is on arXiv.
   This is essential — the arXiv ID will be used to retrieve the full paper.
3. Prioritize high-quality venues: ICML, NeurIPS, ICLR, JMLR, AISTATS, COLT,
   Annals of Mathematics, JAMS, Ann. Statist., PTRF, Nature, Science, PNAS.
4. For each paper, provide a substantive summary of its contributions and relevance.
5. Include the most recent and most cited papers on the topic.

Write a research summary that discusses up to {max_papers} relevant papers.
For each paper, clearly state:
- The full title (in quotes)
- Authors
- Year and venue
- The arXiv ID if available (formatted as arXiv:XXXX.XXXXX)
- Why it is relevant to the query

You may also include a DOI (formatted as doi:10.xxxx/...) when available.
"""


class OpenRouterDeepResearchTool(BaseTool):
    """Comprehensive academic literature search via OpenRouter deep research models."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "deep_literature_search"
    description: str = (
        "Performs comprehensive academic literature search using deep research models. "
        "Searches across arXiv, Semantic Scholar, Google Scholar, and other academic "
        "databases simultaneously. Returns structured paper metadata with titles, "
        "authors, venues, years, abstracts, arXiv IDs, URLs, BibTeX entries, and "
        "relevance summaries. Use this as your PRIMARY tool for finding academic "
        "papers — it replaces separate Semantic Scholar and arXiv queries with a "
        "single comprehensive search."
    )
    args_schema: Type[BaseModel] = OpenRouterDeepResearchInput

    model_name: Optional[str] = None

    def __init__(self, model_name: Optional[str] = None, **kwargs: Any):
        super().__init__(model_name=model_name, **kwargs)

    def _get_model_id(self) -> str:
        """Determine model ID: constructor param > env var > default."""
        if self.model_name:
            return self.model_name
        return os.getenv(
            "DEEP_RESEARCH_MODEL",
            "openrouter/perplexity/sonar-deep-research",
        )

    def _run(
        self,
        query: str,
        max_papers: int = 15,
        focus_area: str = "",
    ) -> str:
        """Search for academic papers using deep research models.

        Returns a JSON string with keys:
            query, papers, bibtex_entries, total_found, search_summary
        """
        if os.getenv("CONSORTIUM_LIVE_SMOKE", "").strip().lower() in {"1", "true", "yes"}:
            max_papers = min(max_papers, int(os.getenv("DEEP_RESEARCH_MAX_PAPERS", "3")))
        # Check response cache first
        ck = _cache_key(query, max_papers, focus_area)
        cached = _cache_get(ck)
        if cached is not None:
            return cached

        model_id = self._get_model_id()

        user_prompt = f"RESEARCH QUERY: {query}"
        if focus_area:
            user_prompt += f"\n\nFOCUS AREA: {focus_area}"
        user_prompt += f"\n\nFind up to {max_papers} relevant academic papers."

        system_prompt = _DEEP_RESEARCH_SYSTEM_PROMPT.replace(
            "{max_papers}", str(max_papers)
        )

        # --- Stage 1: Perplexity discovery ---
        try:
            max_tokens = int(os.getenv("DEEP_RESEARCH_MAX_TOKENS", "16384"))
            resp = litellm.completion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
            raw_content = resp.choices[0].message.content or ""
        except Exception as e:
            print(
                f"[OpenRouterDeepResearchTool] API call failed ({e}), "
                f"falling back to CitationSearchTool."
            )
            return self._fallback_search(query, max_papers)

        # --- Try JSON parse first (works if model returns structured output) ---
        papers = self._parse_response(raw_content)
        if papers is not None:
            papers = self._deduplicate(papers)[:max_papers]
            return self._format_result(query, papers, raw_content)

        # --- Stage 2: Extract paper refs from prose ---
        refs = self._extract_paper_refs(raw_content)
        if refs:
            print(
                f"[OpenRouterDeepResearchTool] Extracted {len(refs)} arXiv IDs "
                f"from prose. Resolving via arXiv API..."
            )
            # --- Stage 3: Resolve via arXiv API ---
            papers = self._resolve_via_arxiv(refs, search_summary=raw_content)
            if papers:
                papers = self._deduplicate(papers)[:max_papers]
                return self._format_result(query, papers, raw_content)

        # --- Last resort: fallback to CitationSearchTool ---
        print(
            "[OpenRouterDeepResearchTool] No arXiv IDs found in prose, "
            "falling back to CitationSearchTool."
        )
        return self._fallback_search(query, max_papers)

    def _format_result(
        self, query: str, papers: List[Dict[str, Any]], raw_content: str,
    ) -> str:
        """Build the final JSON result string and cache it."""
        bibtex_entries = []
        for paper in papers:
            bibtex = self._generate_bibtex(paper)
            if bibtex:
                bibtex_entries.append(bibtex)

        search_summary = self._extract_search_summary(raw_content)

        result = json.dumps(
            {
                "query": query,
                "papers": papers,
                "bibtex_entries": bibtex_entries,
                "total_found": len(papers),
                "search_summary": search_summary,
            },
            indent=2,
        )
        ck = _cache_key(query, len(papers), "")
        _cache_put(ck, result)
        return result

    def _parse_response(self, raw: str) -> Optional[List[Dict[str, Any]]]:
        """Parse the deep research model response into structured paper data."""
        # Try direct JSON parse
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "papers" in data:
                return self._normalize_papers(data["papers"])
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks
        json_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL
        )
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict) and "papers" in data:
                    return self._normalize_papers(data["papers"])
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in the response
        brace_match = re.search(r"\{.*\"papers\"\s*:\s*\[.*\].*\}", raw, re.DOTALL)
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
                if isinstance(data, dict) and "papers" in data:
                    return self._normalize_papers(data["papers"])
            except json.JSONDecodeError:
                pass

        return None

    def _normalize_papers(self, papers: List[Any]) -> List[Dict[str, Any]]:
        """Normalize paper entries to a consistent schema."""
        normalized = []
        for p in papers:
            if not isinstance(p, dict):
                continue
            title = p.get("title", "").strip()
            if not title:
                continue
            paper = {
                "title": title,
                "authors": p.get("authors", []),
                "year": str(p.get("year", "")),
                "venue": p.get("venue", ""),
                "abstract": p.get("abstract", ""),
                "arxiv_id": p.get("arxiv_id", ""),
                "doi": p.get("doi", ""),
                "url": p.get("url", ""),
                "citation_count": p.get("citation_count", 0),
                "relevance_summary": p.get("relevance_summary", ""),
                "source": "deep_research",
            }
            # Clean up arxiv_id if it contains prefix
            if paper["arxiv_id"]:
                aid = re.sub(r"^(?:arxiv:?\s*)", "", paper["arxiv_id"], flags=re.IGNORECASE)
                paper["arxiv_id"] = aid.strip()
            # Ensure authors is a list of strings
            if isinstance(paper["authors"], str):
                paper["authors"] = [a.strip() for a in paper["authors"].split(",")]
            normalized.append(paper)
        return normalized

    def _deduplicate(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate papers based on normalized title."""
        seen = set()
        unique = []
        for paper in papers:
            key = re.sub(r"[^\w]", "", paper["title"].lower())
            if len(key) > 10 and key not in seen:
                seen.add(key)
                unique.append(paper)
        return unique

    def _extract_search_summary(self, raw: str) -> str:
        """Extract search_summary from the raw response."""
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data.get("search_summary", "")
        except (json.JSONDecodeError, TypeError):
            pass
        # Try from code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict):
                    return data.get("search_summary", "")
            except (json.JSONDecodeError, TypeError):
                pass
        return ""

    # ------------------------------------------------------------------
    # Stage 2+3: Extract paper refs from prose → resolve via arXiv API
    # ------------------------------------------------------------------

    def _extract_paper_refs(self, prose: str) -> List[Dict[str, str]]:
        """Extract paper references (arXiv IDs, DOIs, titles) from prose.

        Returns a list of dicts with optional keys: arxiv_id, doi, title.
        """
        refs: List[Dict[str, str]] = []
        seen_ids: set = set()

        # Extract arXiv IDs — e.g. arXiv:2301.12345, arXiv: 2301.12345v2, or bare 2301.12345
        for match in re.finditer(
            r'(?:arXiv:?\s*)(\d{4}\.\d{4,5}(?:v\d+)?)', prose, re.IGNORECASE
        ):
            aid = match.group(1).rstrip(".")
            # Strip version suffix for dedup key
            base_id = re.sub(r'v\d+$', '', aid)
            if base_id not in seen_ids:
                seen_ids.add(base_id)
                refs.append({"arxiv_id": aid})

        # Also catch bare IDs in URL-like contexts: arxiv.org/abs/2301.12345
        for match in re.finditer(
            r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', prose, re.IGNORECASE
        ):
            aid = match.group(1).rstrip(".")
            base_id = re.sub(r'v\d+$', '', aid)
            if base_id not in seen_ids:
                seen_ids.add(base_id)
                refs.append({"arxiv_id": aid})

        return refs

    def _resolve_via_arxiv(
        self, refs: List[Dict[str, str]], search_summary: str = "",
    ) -> Optional[List[Dict[str, Any]]]:
        """Resolve paper references via the arXiv API.

        For each ref with an arxiv_id, queries the arXiv API id_list endpoint
        and parses the XML response into structured paper metadata.

        Returns a list of normalized paper dicts, or None if resolution fails.
        """
        import xml.etree.ElementTree as ET

        arxiv_ids = [r["arxiv_id"] for r in refs if r.get("arxiv_id")]
        if not arxiv_ids:
            return None

        # Batch lookup: arXiv API supports comma-separated id_list
        id_list = ",".join(arxiv_ids)
        url = f"http://export.arxiv.org/api/query?id_list={id_list}&max_results={len(arxiv_ids)}"

        gate = ProviderRateGate("arxiv")
        deadline = time.time() + gate.config.max_wait_seconds
        headers = {"User-Agent": "Academic-Citation-Tool/1.0 (research-tool)"}

        try:
            while True:
                remaining = max(deadline - time.time(), 0.0)
                with gate.request(
                    action=f"arXiv metadata resolution for {len(arxiv_ids)} paper(s)",
                    max_wait_seconds=remaining,
                ) as lease:
                    try:
                        response = requests.get(url, headers=headers, timeout=30)
                    except requests.exceptions.Timeout as exc:
                        lease.mark_saturated(f"arXiv metadata request timed out: {exc}")
                        continue
                    except Exception as exc:
                        lease.mark_failure(f"arXiv metadata request failed: {exc}")
                        print(f"[OpenRouterDeepResearchTool] arXiv API call failed: {exc}")
                        return None

                    if response.status_code == 200:
                        lease.mark_success()
                        break

                    if response.status_code in {429, 500, 502, 503, 504}:
                        lease.mark_saturated(
                            f"arXiv metadata request returned HTTP {response.status_code}",
                            retry_after_seconds=parse_retry_after_seconds(
                                response.headers.get("Retry-After")
                            ),
                        )
                        continue

                    lease.mark_failure(f"arXiv metadata request returned HTTP {response.status_code}")
                    response.raise_for_status()
                    return None
        except ProviderRateLimitTimeout:
            raise
        except Exception as e:
            print(f"[OpenRouterDeepResearchTool] arXiv API call failed: {e}")
            return None

        # Parse XML (same logic as CitationSearchTool._parse_arxiv_response)
        try:
            root = ET.fromstring(response.text)
            papers: List[Dict[str, Any]] = []

            for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                paper: Dict[str, Any] = {
                    "title": "",
                    "authors": [],
                    "year": "",
                    "abstract": "",
                    "url": "",
                    "arxiv_id": "",
                    "doi": "",
                    "venue": "arXiv preprint",
                    "citation_count": 0,
                    "relevance_summary": "",
                    "source": "deep_research+arxiv",
                }

                title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                if title_elem is not None and title_elem.text:
                    paper["title"] = " ".join(title_elem.text.strip().split())

                for author in entry.findall("{http://www.w3.org/2005/Atom}author"):
                    name_elem = author.find("{http://www.w3.org/2005/Atom}name")
                    if name_elem is not None and name_elem.text:
                        paper["authors"].append(name_elem.text.strip())

                published_elem = entry.find("{http://www.w3.org/2005/Atom}published")
                if published_elem is not None and published_elem.text:
                    paper["year"] = published_elem.text[:4]

                summary_elem = entry.find("{http://www.w3.org/2005/Atom}summary")
                if summary_elem is not None and summary_elem.text:
                    paper["abstract"] = " ".join(summary_elem.text.strip().split())

                id_elem = entry.find("{http://www.w3.org/2005/Atom}id")
                if id_elem is not None and id_elem.text:
                    paper["url"] = id_elem.text
                    paper["arxiv_id"] = id_elem.text.split("/")[-1]

                # Extract DOI from arxiv:doi element if present
                doi_elem = entry.find("{http://arxiv.org/schemas/atom}doi")
                if doi_elem is not None and doi_elem.text:
                    paper["doi"] = doi_elem.text.strip()

                # Extract primary category as venue hint
                primary_cat = entry.find("{http://arxiv.org/schemas/atom}primary_category")
                if primary_cat is not None:
                    cat = primary_cat.attrib.get("term", "")
                    if cat:
                        paper["venue"] = f"arXiv preprint ({cat})"

                # Extract journal_ref if published
                journal_ref = entry.find("{http://arxiv.org/schemas/atom}journal_ref")
                if journal_ref is not None and journal_ref.text:
                    paper["venue"] = journal_ref.text.strip()

                if paper["title"]:
                    papers.append(paper)

            if papers:
                print(
                    f"[OpenRouterDeepResearchTool] Resolved {len(papers)}/{len(arxiv_ids)} "
                    f"papers via arXiv API."
                )
            return papers if papers else None

        except Exception as e:
            print(f"[OpenRouterDeepResearchTool] Failed to parse arXiv XML: {e}")
            return None

    @staticmethod
    def _generate_bibtex(paper: Dict[str, Any]) -> Optional[str]:
        """Generate a BibTeX entry for a paper.

        Logic adapted from CitationSearchTool._generate_bibtex.
        """
        title = paper.get("title", "").strip()
        if not title:
            return None

        authors = paper.get("authors", ["Unknown"])
        first_author = authors[0] if authors else "Unknown"
        first_author_last = (
            first_author.split()[-1] if " " in first_author else first_author
        )
        year = paper.get("year", "")

        clean_author = re.sub(r"[^\w]", "", first_author_last.lower())
        citation_key = f"{clean_author}{year}"

        # Choose entry type
        arxiv_id = paper.get("arxiv_id", "")
        venue = paper.get("venue", "").lower()
        if arxiv_id:
            entry_type = "misc"
            note_field = f"arXiv preprint arXiv:{arxiv_id}"
        elif "conference" in venue or venue in {
            "icml", "neurips", "nips", "iclr", "aaai", "ijcai", "cvpr",
            "eccv", "iccv", "acl", "emnlp", "naacl", "aistats", "colt",
        }:
            entry_type = "inproceedings"
            note_field = ""
        elif "journal" in venue or venue in {
            "jmlr", "tmlr", "nature", "science", "pnas",
        }:
            entry_type = "article"
            note_field = ""
        else:
            entry_type = "misc"
            note_field = ""

        author_str = " and ".join(authors) if authors else "Unknown"

        lines = [f"@{entry_type}{{{citation_key},"]
        lines.append(f"  title = {{{title}}},")
        lines.append(f"  author = {{{author_str}}},")
        if year:
            lines.append(f"  year = {{{year}}},")
        if paper.get("venue"):
            raw_venue = paper["venue"]
            if entry_type == "inproceedings":
                lines.append(f"  booktitle = {{{raw_venue}}},")
            elif entry_type == "article":
                lines.append(f"  journal = {{{raw_venue}}},")
        if note_field:
            lines.append(f"  note = {{{note_field}}},")
        if paper.get("url"):
            lines.append(f"  url = {{{paper['url']}}},")
        if paper.get("doi"):
            lines.append(f"  doi = {{{paper['doi']}}},")
        lines.append("}")

        return "\n".join(lines)

    def _fallback_search(self, query: str, max_papers: int) -> str:
        """Fall back to staged provider search when deep research fails."""
        try:
            from ...writeup.citation_search_tool import CitationSearchTool

            fallback = CitationSearchTool()
            arxiv_result = json.loads(
                fallback._run(
                    search_query=query,
                    max_results=max_papers,
                    search_source="arxiv",
                )
            )

            arxiv_citations = arxiv_result.get("citations", []) if isinstance(arxiv_result, dict) else []
            coverage_target = min(max_papers, max(3, max_papers // 2))
            needs_semantic_enrichment = (
                self._is_targeted_citation_query(query)
                or len(arxiv_citations) < coverage_target
            )
            if not needs_semantic_enrichment:
                return json.dumps(arxiv_result, indent=2)

            semantic_result = json.loads(
                fallback._run(
                    search_query=query,
                    max_results=max_papers,
                    search_source="semantic_scholar",
                )
            )
            merged_citations = fallback._deduplicate_citations(
                list(arxiv_citations)
                + list(semantic_result.get("citations", []))
            )[:max_papers]
            merged_bibtex = []
            for citation in merged_citations:
                bibtex = fallback._generate_bibtex(citation)
                if bibtex:
                    merged_bibtex.append(bibtex)

            result = {
                "search_query": query,
                "search_source": "arxiv+semantic_scholar_staged",
                "total_results": len(merged_citations),
                "citations": merged_citations,
                "bibtex_entries": merged_bibtex,
                "usage_instructions": {
                    "latex_integration": "Copy BibTeX entries to your .bib file and use \\cite{key} in LaTeX",
                    "citation_keys": [
                        re.search(r"@\w+\{([^,]+),", bibtex).group(1)
                        for bibtex in merged_bibtex
                        if re.search(r"@\w+\{([^,]+),", bibtex)
                    ],
                },
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            if isinstance(e, ProviderRateLimitTimeout):
                raise
            print(f"[OpenRouterDeepResearchTool] Fallback also failed: {e}")
            return json.dumps(
                {
                    "query": query,
                    "papers": [],
                    "bibtex_entries": [],
                    "total_found": 0,
                    "search_summary": f"Search unavailable: {e}",
                }
            )

    @staticmethod
    def _is_targeted_citation_query(query: str) -> bool:
        normalized = query.strip()
        if not normalized:
            return False
        if re.search(r"(?:arxiv:)?\s*\d{4}\.\d{4,5}(?:v\d+)?", normalized, re.IGNORECASE):
            return True
        if re.search(r"\b10\.\d{4,9}/\S+\b", normalized):
            return True
        if normalized.count('"') >= 2 or normalized.count("'") >= 2:
            return True
        return False
