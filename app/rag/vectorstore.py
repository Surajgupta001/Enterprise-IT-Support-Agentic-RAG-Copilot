import os
import time

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

from app.core.config import get_settings

settings = get_settings()


_embeddings = None
_vectorstore = None


EMBEDDING_DIMENSIONS = {
    "sentence-transformers/all-minilm-l6-v2": 384,
    "all-minilm-l6-v2": 384,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def get_embedding_dimension(model_name: str | None = None) -> int:
    name = (model_name or settings.embedding_model or "").strip()
    if not name:
        raise RuntimeError("Embedding model is not configured")

    normalized = name.lower()
    if normalized in EMBEDDING_DIMENSIONS:
        return EMBEDDING_DIMENSIONS[normalized]
    if "text-embedding-3-small" in normalized:
        return 1536
    if "text-embedding-3-large" in normalized:
        return 3072
    if "text-embedding-ada-002" in normalized:
        return 1536
    if "all-minilm" in normalized:
        return 384

    raise ValueError(
        f"Unsupported embedding model '{model_name or settings.embedding_model}' for Pinecone. "
        "Add the matching dimension to EMBEDDING_DIMENSIONS."
    )


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        # Free local embeddings — no API key / no per-token cost.
        # Runs on CPU via sentence-transformers.
        _embeddings = HuggingFaceEmbeddings(
            model_name=settings.embedding_model,
        )
    return _embeddings


def _list_index_names(pc) -> list[str]:
    """Compatible with pinecone>=5 (objects with .name) and older dict-style returns."""
    names: list[str] = []
    for x in pc.list_indexes():
        if isinstance(x, dict):
            names.append(x.get("name", ""))
        else:
            names.append(getattr(x, "name", str(x)))
    return names


def _is_index_ready(pc, index_name: str) -> bool:
    info = pc.describe_index(index_name)
    status = getattr(info, "status", None)
    if isinstance(status, dict):
        return bool(status.get("ready"))
    if status is not None:
        return bool(getattr(status, "ready", False))
    if isinstance(info, dict):
        return bool(info.get("status", {}).get("ready"))
    return False


def ensure_index():
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing")

    # Ensure env var is set for libraries that auto-detect it
    os.environ.setdefault("PINECONE_API_KEY", settings.pinecone_api_key)

    desired_dimension = get_embedding_dimension()
    pc = Pinecone(api_key=settings.pinecone_api_key)
    names = _list_index_names(pc)

    if settings.pinecone_index_name in names:
        index_info = pc.describe_index(settings.pinecone_index_name)
        current_dimension = getattr(index_info, "dimension", None)
        if current_dimension is None and isinstance(index_info, dict):
            current_dimension = index_info.get("dimension")

        if current_dimension is not None and current_dimension != desired_dimension:
            pc.delete_index(name=settings.pinecone_index_name)
            while settings.pinecone_index_name in _list_index_names(pc):
                time.sleep(1)

    if settings.pinecone_index_name not in _list_index_names(pc):
        pc.create_index(
            name=settings.pinecone_index_name,
            dimension=desired_dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        while not _is_index_ready(pc, settings.pinecone_index_name):
            time.sleep(1)

    return pc.Index(settings.pinecone_index_name)


def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        index = ensure_index()
        _vectorstore = PineconeVectorStore(
            index=index,
            embedding=get_embeddings(),
            namespace=settings.pinecone_namespace,
        )
    return _vectorstore


def get_retriever():
    return get_vectorstore().as_retriever(search_kwargs={"k": settings.top_k})


def add_documents(chunks):
    store = get_vectorstore()
    return store.add_documents(chunks)
