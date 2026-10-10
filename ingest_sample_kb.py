from pathlib import Path

from dotenv import load_dotenv

# Load .env early — API keys must be available before modules init
load_dotenv()

from app.core.config import get_settings
from app.rag.vectorstore import add_documents
from app.services.ingestion import SUPPORTED, chunk_documents, load_file


def main() -> None:
    settings = get_settings()
    folder = Path(settings.sample_kb_dir)
    if not folder.is_dir():
        raise SystemExit(f"Sample KB dir not found: {folder}")
    files = [
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED
    ]
    if not files:
        raise SystemExit(f"No supported files ({sorted(SUPPORTED)}) in {folder}")
    all_docs = []
    for path in files:
        all_docs.extend(load_file(path))
    if not all_docs:
        raise SystemExit("No documents loaded — check sample KB files.")
    chunks = chunk_documents(all_docs)
    ids = add_documents(chunks)
    print(
        f"Indexed {len(files)} files -> {len(chunks)} chunks -> {len(ids)} Pinecone vectors"
    )


if __name__ == "__main__":
    main()
