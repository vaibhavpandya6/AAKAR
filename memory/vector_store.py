"""Vector store for semantic code search using ChromaDB."""

import logging
import re
from pathlib import Path
from typing import List, Optional

import chromadb
import structlog
import tiktoken
from chromadb.config import Settings

from config import settings
from workspace_manager import get_workspace_manager

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()

# Token counting for chunking
_TOKENIZER = tiktoken.encoding_for_model("text-embedding-3-small")

# Regex patterns for code structure boundaries
CODE_STRUCTURE_PATTERNS = [
    r"^\s*(async\s+)?def\s+\w+",  # Python functions
    r"^\s*class\s+\w+",  # Python classes
    r"^\s*async\s+function\s+\w+",  # JavaScript async functions
    r"^\s*function\s+\w+",  # JavaScript functions
    r"^\s*export\s+(default\s+)?(function|const|class|async)",  # JS exports
]


class VectorStore:
    """Semantic code search using ChromaDB embeddings."""

    def __init__(self):
        """Initialize ChromaDB client."""
        # Local persistent storage
        chroma_db_path = Path(settings.workspace_base_path).parent / "chroma_db"
        chroma_db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(chroma_db_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
        logger.info("ChromaDB client initialized", path=str(chroma_db_path))

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text.

        Args:
            text: Text to count.

        Returns:
            Token count.
        """
        try:
            return len(_TOKENIZER.encode(text))
        except Exception:
            # Fallback: rough estimate (1 token ≈ 4 chars)
            return len(text) // 4

    def _chunk_by_structure(self, content: str, max_chunk_tokens: int = 500) -> List[str]:
        """Chunk content at code structure boundaries (functions/classes).

        Args:
            content: Code content to chunk.
            max_chunk_tokens: Max tokens per chunk (soft limit).

        Returns:
            List of code chunks.
        """
        lines = content.split("\n")
        chunks = []
        current_chunk = []
        current_tokens = 0

        for line in lines:
            line_tokens = self._count_tokens(line)

            # Check if line is a structure boundary (function/class def)
            is_boundary = any(re.match(pattern, line) for pattern in CODE_STRUCTURE_PATTERNS)

            # Start new chunk if: boundary found and chunk not empty, or token limit exceeded
            if is_boundary and current_chunk and current_tokens > 0:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_tokens = line_tokens
            elif current_tokens + line_tokens > max_chunk_tokens and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_tokens = line_tokens
            else:
                current_chunk.append(line)
                current_tokens += line_tokens

        # Add remaining chunk
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return [c for c in chunks if c.strip()]

    def _chunk_with_overlap(
        self, content: str, chunk_size: int = 500, overlap: int = 50
    ) -> List[str]:
        """Chunk content with sliding window (fallback method).

        Args:
            content: Content to chunk.
            chunk_size: Chunk size in tokens.
            overlap: Overlap size in tokens.

        Returns:
            List of overlapping chunks.
        """
        tokens = _TOKENIZER.encode(content)
        chunks = []

        stride = chunk_size - overlap
        for i in range(0, len(tokens), stride):
            chunk_tokens = tokens[i : i + chunk_size]
            if chunk_tokens:
                try:
                    chunk_text = _TOKENIZER.decode(chunk_tokens)
                    chunks.append(chunk_text)
                except Exception:
                    pass

        return chunks

    def _split_content(self, content: str) -> List[tuple[str, int]]:
        """Split content into chunks with fallback strategy.

        Args:
            content: Content to chunk.

        Returns:
            List of (chunk_text, chunk_index) tuples.
        """
        # Try structure-based chunking first
        chunks = self._chunk_by_structure(content)

        # If only one chunk or too few chunks, use sliding window
        if len(chunks) < 2:
            chunks = self._chunk_with_overlap(content)

        return [(chunk, idx) for idx, chunk in enumerate(chunks)]

    async def index_file(
        self, project_id: str, file_path: str, content: str, task_id: str = ""
    ) -> int:
        """Index file content into vector store.

        Args:
            project_id: Project identifier.
            file_path: Relative file path.
            content: File content to index.
            task_id: Optional task ID for metadata.

        Returns:
            Number of chunks indexed.

        Raises:
            Exception: If indexing fails.
        """
        try:
            await struct_logger.ainfo(
                "file_indexing_started",
                project_id=project_id,
                file_path=file_path,
                size=len(content),
            )

            # Get collection
            collection_name = f"project_{project_id}"
            collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            # Split content
            chunks = self._split_content(content)

            # Index each chunk
            for chunk_text, chunk_idx in chunks:
                if not chunk_text.strip():
                    continue

                chunk_id = f"{file_path}_{chunk_idx}"

                collection.upsert(
                    ids=[chunk_id],
                    documents=[chunk_text],
                    metadatas=[
                        {
                            "file_path": file_path,
                            "chunk_index": str(chunk_idx),
                            "task_id": task_id,
                            "file_size": str(len(content)),
                        }
                    ],
                )

            await struct_logger.ainfo(
                "file_indexing_completed",
                project_id=project_id,
                file_path=file_path,
                chunks=len(chunks),
            )

            return len(chunks)

        except Exception as e:
            logger.error(
                "Failed to index file",
                project_id=project_id,
                file_path=file_path,
                error=str(e),
            )
            raise

    async def retrieve(
        self, project_id: str, query: str, top_k: int = 5
    ) -> List[dict]:
        """Retrieve similar code chunks.

        Args:
            project_id: Project identifier.
            query: Search query.
            top_k: Number of results to return.

        Returns:
            List of dicts with keys: content, file_path, similarity_score
        """
        try:
            collection_name = f"project_{project_id}"

            # Get collection (create if not exists)
            try:
                collection = self.client.get_collection(collection_name)
            except Exception:
                logger.debug(
                    "Collection not found, returning empty results",
                    project_id=project_id,
                )
                return []

            # Query
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )

            if not results or not results["documents"] or not results["documents"][0]:
                return []

            # Format results
            formatted = []
            for doc, metadata, distance in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                # Distances are dissimilarity, convert to similarity
                similarity = 1 - distance if distance is not None else 0
                formatted.append(
                    {
                        "content": doc,
                        "file_path": metadata.get("file_path"),
                        "similarity_score": similarity,
                        "chunk_index": metadata.get("chunk_index"),
                        "task_id": metadata.get("task_id"),
                    }
                )

            logger.debug(
                "Retrieval completed",
                project_id=project_id,
                query_len=len(query),
                results=len(formatted),
            )
            return formatted

        except Exception as e:
            logger.error(
                "Retrieval failed",
                project_id=project_id,
                error=str(e),
            )
            return []

    async def index_workspace(self, project_id: str) -> int:
        """Re-index all files in workspace.

        Args:
            project_id: Project identifier.

        Returns:
            Total number of chunks indexed.
        """
        try:
            ws_manager = await get_workspace_manager()
            files = await ws_manager.list_files(project_id)

            total_chunks = 0

            for file_path in files:
                # Skip non-text files
                if file_path.endswith((".json", ".md", ".txt", ".py", ".js", ".ts", ".jsx", ".tsx")):
                    try:
                        content = await ws_manager.read_file(project_id, file_path)
                        chunks = await self.index_file(project_id, file_path, content)
                        total_chunks += chunks
                    except Exception as e:
                        logger.warning(
                            "Failed to index file in workspace",
                            file_path=file_path,
                            error=str(e),
                        )

            logger.info(
                "Workspace indexing completed",
                project_id=project_id,
                files=len(files),
                total_chunks=total_chunks,
            )
            return total_chunks

        except Exception as e:
            logger.error(
                "Workspace indexing failed",
                project_id=project_id,
                error=str(e),
            )
            raise

    async def delete_project(self, project_id: str) -> None:
        """Delete project collection.

        Args:
            project_id: Project identifier.
        """
        try:
            collection_name = f"project_{project_id}"
            self.client.delete_collection(collection_name)
            logger.info("Project collection deleted", project_id=project_id)
        except Exception as e:
            logger.warning(
                "Failed to delete project collection",
                project_id=project_id,
                error=str(e),
            )


# Global vector store instance
_vector_store_instance: Optional[VectorStore] = None


async def get_vector_store() -> VectorStore:
    """Get or create global vector store instance.

    Returns:
        VectorStore instance.
    """
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = VectorStore()
    return _vector_store_instance
