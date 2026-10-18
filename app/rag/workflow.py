import json
import logging
import os
import re
from typing import Any, Literal

from langchain_groq import ChatGroq
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.rag.state import AgentState, EvidenceGrade, RouteDecision
from app.rag.vectorstore import get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()

_llms: dict[str, ChatGroq] = {}
_web_search = None

# Keep prompts small enough for Groq free-tier context limits.
MAX_CONTEXT_CHARS = 12000


def llm(model: str | None = None):
    """Get (and cache) a ChatGroq for the given model, defaulting to settings."""
    name = model or settings.groq_model
    if name not in _llms:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is missing")
        # Ensure env var is set for libraries that auto-detect it
        os.environ.setdefault("GROQ_API_KEY", settings.groq_api_key)
        _llms[name] = ChatGroq(
            model=name,
            temperature=0,
            api_key=settings.groq_api_key,
        )
    return _llms[name]


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "model_not_found" in msg or (
        "404" in msg and ("does not exist" in msg or "model" in msg)
    )


def _invoke_with_fallback(make_runnable, payload: str):
    """Invoke an LLM runnable, falling back to a second model on 404 model_not_found.

    Groq retires model IDs (e.g. llama-3.3-70b-versatile); without this the
    whole chat returns 500. Raises a clear error only if both models fail.
    """
    primary = settings.groq_model
    try:
        return make_runnable(llm(primary)).invoke(payload)
    except Exception as exc:
        fallback = settings.groq_fallback_model
        if not fallback or fallback == primary or not _is_model_not_found(exc):
            raise
        logger.warning(
            "Primary Groq model %r unavailable (%s); retrying with fallback %r",
            primary,
            exc,
            fallback,
        )
        try:
            return make_runnable(llm(fallback)).invoke(payload)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Groq models {primary!r} and {fallback!r} are both unavailable: {fallback_exc}. "
                "Set GROQ_MODEL in .env to a model listed at https://console.groq.com/docs/models "
                "(e.g. openai/gpt-oss-20b or llama-3.1-8b-instant) and restart the server."
            ) from fallback_exc


def _chat(prompt: str) -> str:
    return _text_of(_invoke_with_fallback(lambda m: m, prompt))


def _is_tool_use_failed(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "tool_use_failed" in msg or "did not call a tool" in msg


def _manual_schema_parse(schema, exc: Exception):
    """Last-resort parse: gpt-oss often returns valid JSON as plain text
    (see failed_generation in the 400 error) instead of calling the tool."""
    text = str(exc)
    candidates: list[str] = []
    m = re.search(r"failed_generation['\"]\s*:\s*['\"](\{.*?\})['\"]", text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    candidates.extend(m.group(0) for m in re.finditer(r"\{[^{}]+\}", text))
    for c in candidates:
        try:
            return schema.model_validate(json.loads(c))
        except Exception:
            continue
    raise ValueError(f"Could not parse structured output from: {text[:500]}")


def _structured_invoke(schema, payload: str):
    """Structured output resilient to Groq model quirks.

    Strategy per model: tool-calling first, then json_mode on
    tool_use_failed, then manual parse of failed_generation JSON.
    Then repeat on the fallback model for model_not_found.
    """
    models = [settings.groq_model]
    if settings.groq_fallback_model and settings.groq_fallback_model != models[0]:
        models.append(settings.groq_fallback_model)
    errors: list[Exception] = []
    for name in models:
        model = llm(name)
        for method in ("function_calling", "json_mode"):
            try:
                return model.with_structured_output(schema, method=method).invoke(
                    payload
                )
            except Exception as exc:
                errors.append(exc)
                if _is_model_not_found(exc):
                    break  # dead model id → try next model
                if method == "function_calling" and _is_tool_use_failed(exc):
                    logger.warning(
                        "Model %r skipped tool call; retrying with json_mode", name
                    )
                    continue  # same model, json_mode next
                try:
                    return _manual_schema_parse(schema, exc)
                except Exception:
                    continue  # try next method/model
    try:
        return _manual_schema_parse(schema, errors[-1])
    except Exception:
        pass
    raise RuntimeError(
        f"Structured output failed on {models}: {errors[-1]}. "
        "Set GROQ_MODEL in .env to a model listed at "
        "https://console.groq.com/docs/models and restart the server."
    ) from errors[-1]


def structured_llm(schema):
    """Groq-compatible structured output.

    gpt-oss models ignore forced tool choice and return raw JSON text
    (400 tool_use_failed); llama models prefer tool calling. The helper
    below tries both plus a manual-parse fallback.
    """
    return _StructuredWithFallback(schema)


class _StructuredWithFallback:
    """Structured-output runnable with model + method fallback."""

    def __init__(self, schema):
        self._schema = schema

    def invoke(self, payload: str):
        return _structured_invoke(self._schema, payload)


def web_search_tool():
    global _web_search
    if _web_search is None:
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY is missing")
        # TavilySearch reads from TAVILY_API_KEY env var
        os.environ.setdefault("TAVILY_API_KEY", settings.tavily_api_key)
        _web_search = TavilySearch(
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


def _text_of(message: Any) -> str:
    """Extract plain text from an LLM message (str or list of blocks)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


def add_trace(state: AgentState, message: str):
    return [*state.get("trace", []), message]


def _kb_context(state: AgentState) -> str:
    context = "\n\n".join(
        f"Source: {d.metadata.get('source', 'unknown')}\n{d.page_content}"
        for d in state.get("kb_docs", [])
    )
    return context[:MAX_CONTEXT_CHARS]


def route_question(state: AgentState):
    router = structured_llm(RouteDecision)
    decision = router.invoke(f"""
You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {state["question"]}
Return valid JSON like {{"route":"kb"}}.
""")
    return {
        "source_used": decision.route,
        "trace": add_trace(state, f"Router → {decision.route.upper()}"),
    }


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state.get("source_used") == "kb" else "direct_answer"


def retrieve_kb(state: AgentState):
    docs = get_retriever().invoke(state["current_query"])
    if not docs:
        logger.warning(
            "Private KB retrieval returned 0 chunks for query %r — "
            "the Pinecone index may be empty. Re-run: python ingest_sample_kb.py",
            state["current_query"][:100],
        )
    return {
        "kb_docs": docs,
        "trace": add_trace(state, f"Private KB retrieval → {len(docs)} chunks"),
    }


def grade_kb(state: AgentState):
    if not state.get("kb_docs"):
        return {
            "kb_grade": "weak",
            "trace": add_trace(state, "KB evidence grade → WEAK (no chunks)"),
        }
    grader = structured_llm(EvidenceGrade)
    grade = grader.invoke(f"""
You grade evidence for an enterprise IT support assistant.
Question: {state["question"]}
Private company KB evidence:\n{_kb_context(state)}
Return good only if the evidence is sufficient to answer confidently and specifically.
Otherwise return weak. JSON: {{"grade":"good"}} or {{"grade":"weak"}}.
""")
    return {
        "kb_grade": grade.grade,
        "trace": add_trace(state, f"KB evidence grade → {grade.grade.upper()}"),
    }


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state.get("kb_grade") == "good" else "search_web"


def search_web(state: AgentState):
    result = web_search_tool().invoke({"query": state["current_query"]})
    lines, citations = [], []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + str(result["answer"]))
        items = result.get("results", []) or []
        for item in items:
            if not isinstance(item, dict):
                lines.append(str(item))
                continue
            title, url, content = (
                item.get("title", ""),
                item.get("url", ""),
                item.get("content", ""),
            )
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
            citations.append({"title": title or url, "url": url, "type": "web"})
    elif isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                title = item.get("title", "") or item.get("url", "")
                url = item.get("url", "")
                lines.append(
                    f"Title: {title}\nURL: {url}\nContent: {item.get('content', '')}"
                )
                citations.append({"title": title or url, "url": url, "type": "web"})
            else:
                lines.append(str(item))
    elif isinstance(result, str):
        lines.append(result)
    else:
        lines.append(str(result))
    return {
        "web_results": "\n\n".join(lines)[:MAX_CONTEXT_CHARS],
        "citations": citations,
        "source_used": "web",
        "trace": add_trace(state, "Web fallback → Tavily search"),
    }


def grade_web(state: AgentState):
    if not (state.get("web_results") or "").strip():
        return {
            "web_grade": "weak",
            "trace": add_trace(state, "Web evidence grade → WEAK (no results)"),
        }
    grader = structured_llm(EvidenceGrade)
    grade = grader.invoke(f"""
Question: {state["question"]}
Web evidence:\n{state["web_results"][:MAX_CONTEXT_CHARS]}
Return good if the evidence is sufficient and directly relevant; otherwise weak.
Return valid JSON like {{"grade":"good"}}.
""")
    return {
        "web_grade": grade.grade,
        "trace": add_trace(state, f"Web evidence grade → {grade.grade.upper()}"),
    }


def after_web(
    state: AgentState,
) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state.get("web_grade") == "good":
        return "generate_from_web"
    if state.get("retry_count", 0) < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


def rewrite_query(state: AgentState):
    rewritten = _chat(f"""
Rewrite this IT support question for better private knowledge retrieval and vendor web search.
Preserve intent, add useful technical keywords, do not answer, return only the query.
Question: {state["question"]}
""").strip()
    if not rewritten:
        rewritten = state["question"]
    return {
        "current_query": rewritten,
        "retry_count": state.get("retry_count", 0) + 1,
        "trace": add_trace(state, f"Query rewrite → {rewritten}"),
    }


def generate_from_kb(state: AgentState):
    context = "\n\n".join(
        f"[Source: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in state.get("kb_docs", [])
    )[:MAX_CONTEXT_CHARS]
    answer = _chat(f"""
You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {state["question"]}\n\nPrivate KB:\n{context}
""")
    citations = []
    seen = set()
    for d in state.get("kb_docs", []):
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append(
                {"title": src.split("/")[-1], "url": "", "type": "private_kb"}
            )
    return {
        "answer": answer,
        "source_used": "private_kb",
        "citations": citations,
        "trace": add_trace(state, "Answer generation → PRIVATE KB"),
    }


def generate_from_web(state: AgentState):
    answer = _chat(f"""
You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {state["question"]}\n\nWeb evidence:\n{state.get("web_results", "")[:MAX_CONTEXT_CHARS]}
""")
    return {
        "answer": answer,
        "source_used": "web_search",
        "trace": add_trace(state, "Answer generation → WEB SEARCH"),
    }


def direct_answer(state: AgentState):
    answer = _chat(f"Respond briefly and naturally to: {state['question']}")
    return {
        "answer": answer,
        "source_used": "direct",
        "trace": add_trace(state, "Direct response → no retrieval"),
    }


def insufficient(state: AgentState):
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": add_trace(state, "Stopped → insufficient reliable evidence"),
    }


def build_graph():
    graph = StateGraph(AgentState)
    for name, fn in {
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "route_question")
    graph.add_conditional_edges(
        "route_question",
        route_after_router,
        {"retrieve_kb": "retrieve_kb", "direct_answer": "direct_answer"},
    )
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges(
        "grade_kb",
        after_kb,
        {"generate_from_kb": "generate_from_kb", "search_web": "search_web"},
    )
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges(
        "grade_web",
        after_web,
        {
            "generate_from_web": "generate_from_web",
            "rewrite_query": "rewrite_query",
            "insufficient": "insufficient",
        },
    )
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)
    return graph.compile()


agent_graph = build_graph()


def ask(question: str):
    if not question or not question.strip():
        raise ValueError("Question must not be empty")
    initial: AgentState = {
        "question": question.strip(),
        "current_query": question.strip(),
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
    }
    return agent_graph.invoke(initial)
