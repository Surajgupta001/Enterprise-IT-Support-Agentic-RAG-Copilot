from typing import Literal

from langchain_core.documents import Document
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class RouteDecision(BaseModel):
    route: Literal["kb", "direct"] = Field(
        description="kb for IT/support questions; direct for greetings/simple chat"
    )


class EvidenceGrade(BaseModel):
    grade: Literal["good", "weak"] = Field(
        description="Whether evidence is sufficient to answer"
    )


class AgentState(TypedDict):
    question: str
    current_query: str
    kb_docs: list[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    answer: str
    source_used: str
    retry_count: int
    trace: list[str]
    citations: list[dict]
