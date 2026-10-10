from collections.abc import Iterable
from pathlib import Path

from docx import Document as DocxDocument
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

SUPPORTED = {".pdf", ".txt", ".md", ".docx"}


def _with_source(docs: list[Document], path: Path) -> list[Document]:
    """Normalize metadata so citations show the file name, not an absolute path."""
    for d in docs:
        d.metadata.setdefault("source", path.name)
    return docs


def load_file(path: Path) -> list[Document]:
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(
            f"Unsupported file type: {suffix}. Supported: {sorted(SUPPORTED)}"
        )

    if suffix == ".pdf":
        docs = PyPDFLoader(str(path)).load()
        docs = [d for d in docs if d.page_content and d.page_content.strip()]
        if not docs:
            raise ValueError(f"No extractable text found in PDF: {path.name}")
        return _with_source(docs, path)

    if suffix in {".txt", ".md"}:
        try:
            docs = TextLoader(str(path), encoding="utf-8").load()
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Could not decode {path.name} as UTF-8. Save it as UTF-8 and retry."
            ) from exc
        docs = [d for d in docs if d.page_content and d.page_content.strip()]
        if not docs:
            raise ValueError(f"File is empty (no text content): {path.name}")
        return _with_source(docs, path)

    if suffix == ".docx":
        try:
            doc = DocxDocument(str(path))
        except Exception as exc:
            raise ValueError(
                f"Could not read .docx file {path.name} (is it a valid Word document?)"
            ) from exc
        text = "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
        if not text.strip():
            raise ValueError(f"File is empty (no text content): {path.name}")
        return [Document(page_content=text, metadata={"source": path.name})]

    raise ValueError(f"Unsupported file type: {suffix}")


def chunk_documents(docs: Iterable[Document]) -> list[Document]:
    docs = [d for d in list(docs) if d.page_content and d.page_content.strip()]
    if not docs:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900, chunk_overlap=120, add_start_index=True
    )
    return splitter.split_documents(docs)
