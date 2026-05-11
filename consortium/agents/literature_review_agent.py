"""
LiteratureReviewAgent — LangGraph node module.
"""

from __future__ import annotations

import os
from typing import Any, Callable, List, Optional

from ..agents.base_agent import create_specialist_agent
from ..prompts.literature_review_instructions import get_literature_review_system_prompt
from ..toolkits.filesystem.file_editing.file_editing_tools import (
    CreateFileWithContent, DeleteFileOrFolder, ListDir, ModifyFile, SearchKeyword, SeeFile,
)
from ..toolkits.search.deep_research.openrouter_deep_research_tool import OpenRouterDeepResearchTool
from ..toolkits.search.fetch_arxiv_papers.fetch_arxiv_papers_tools import FetchArxivPapersTool
from ..toolkits.writeup.citation_search_tool import CitationSearchTool
from ..toolkits.writeup.latex_compiler_tool import LaTeXCompilerTool
from ..toolkits.writeup.latex_generator_tool import LaTeXGeneratorTool
from ..toolkits.writeup.latex_syntax_checker_tool import LaTeXSyntaxCheckerTool
from ..toolkits.writeup.vlm_document_analysis_tool import VLMDocumentAnalysisTool
from ..toolkits.code_execution_tool import PythonCodeExecutionTool

try:
    from ..toolkits.search.open_deep_search.ods_tool import OpenDeepSearchTool
except (ImportError, ModuleNotFoundError):
    OpenDeepSearchTool = None

try:
    from ..toolkits.search.deep_research.deep_research_tool import DeepResearchNoveltyScanTool
except (ImportError, ModuleNotFoundError):
    DeepResearchNoveltyScanTool = None


def get_tools(workspace_dir: Optional[str], model_id: str) -> list:
    from . import tool_registry as _reg
    tools = [
        _reg.get_or_create(OpenRouterDeepResearchTool),
        _reg.get_or_create(FetchArxivPapersTool, working_dir=workspace_dir),
        _reg.get_or_create(CitationSearchTool),
        _reg.get_or_create(VLMDocumentAnalysisTool, model=model_id, working_dir=workspace_dir),
        _reg.get_or_create(LaTeXGeneratorTool, model=model_id, working_dir=workspace_dir),
        _reg.get_or_create(LaTeXCompilerTool, model=model_id, working_dir=workspace_dir),
        _reg.get_or_create(LaTeXSyntaxCheckerTool, working_dir=workspace_dir),
    ]
    if OpenDeepSearchTool is not None:
        tools.insert(2, _reg.get_or_create(OpenDeepSearchTool, model_name=model_id))
    else:
        research_model = os.getenv("DEEP_RESEARCH_MODEL", "openrouter/perplexity/sonar-deep-research")
        print(
            "[literature_review_agent] INFO: OpenDeepSearchTool disabled - "
            f"using OpenRouterDeepResearchTool ({research_model}) as primary."
        )
    if DeepResearchNoveltyScanTool is not None:
        tools.insert(3, _reg.get_or_create(DeepResearchNoveltyScanTool, model_name=model_id))
    else:
        print("⚠️ DeepResearchNoveltyScanTool disabled for LiteratureReviewAgent.")
    if workspace_dir:
        tools += [
            _reg.get_or_create(SeeFile, working_dir=workspace_dir),
            _reg.get_or_create(CreateFileWithContent, working_dir=workspace_dir),
            _reg.get_or_create(ModifyFile, working_dir=workspace_dir),
            _reg.get_or_create(ListDir, working_dir=workspace_dir),
            _reg.get_or_create(SearchKeyword, working_dir=workspace_dir),
            _reg.get_or_create(DeleteFileOrFolder, working_dir=workspace_dir),
            _reg.get_or_create(PythonCodeExecutionTool, workspace_dir=workspace_dir, authorized_imports=[]),
        ]
    return tools


def build_node(
    model: Any,
    workspace_dir: Optional[str],
    authorized_imports: Optional[List[str]] = None,
    **cfg: Any,
) -> Callable:
    from ..toolkits.model_utils import get_raw_model
    model_id = get_raw_model(model)
    tools = get_tools(workspace_dir, model_id)
    system_prompt = get_literature_review_system_prompt(tools=tools, managed_agents=None)
    counsel_models = cfg.get("counsel_models")
    if counsel_models is not None:
        from ..counsel import create_counsel_node
        return create_counsel_node(system_prompt, tools, "literature_review_agent", workspace_dir, counsel_models)
    return create_specialist_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        agent_name="literature_review_agent",
        workspace_dir=workspace_dir,
    )
