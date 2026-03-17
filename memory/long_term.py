"""Long-term learning from bug fixes and errors."""

import logging
from typing import List, Optional

import chromadb
import structlog

from config import settings
from chromadb.config import Settings

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger()


class LongTermMemory:
    """Learn from past fixes and retrieve solutions for similar errors.

    Uses ChromaDB to store and retrieve bug fixes and patterns learned
    across all projects.
    """

    COLLECTION_NAME = "long_term_patterns"

    def __init__(self):
        """Initialize long-term memory store."""
        # Use same persistent storage as vector store
        from pathlib import Path

        chroma_db_path = Path(settings.workspace_base_path).parent / "chroma_db"
        chroma_db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(chroma_db_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        logger.info("Long-term memory initialized")

    async def store_fix(
        self,
        task_id: str,
        error: str,
        fix: str,
        agent: str,
    ) -> str:
        """Store learned bug fix in long-term memory.

        Args:
            task_id: Task ID where fix was discovered.
            error: Error message or pattern.
            fix: Description/implementation of the fix.
            agent: Agent that discovered the fix.

        Returns:
            Memory ID for the stored fix.

        Raises:
            Exception: If storage fails.
        """
        try:
            await struct_logger.ainfo(
                "fix_storage_started",
                task_id=task_id,
                agent=agent,
                error_sample=error[:100],
            )

            # Use error as the document for embedding
            # Store fix and context in metadata
            memory_id = f"fix_{task_id}_{agent}"

            self.collection.upsert(
                ids=[memory_id],
                documents=[error],  # Indexed for similarity search
                metadatas=[
                    {
                        "task_id": task_id,
                        "agent": agent,
                        "fix": fix,
                        "error_type": self._classify_error(error),
                    }
                ],
            )

            await struct_logger.ainfo(
                "fix_storage_completed",
                task_id=task_id,
                memory_id=memory_id,
            )

            return memory_id

        except Exception as e:
            logger.error(
                "Failed to store fix",
                task_id=task_id,
                error=str(e),
            )
            raise

    async def retrieve_similar_fixes(
        self,
        error: str,
        top_k: int = 3,
    ) -> List[dict]:
        """Retrieve past fixes for similar errors.

        Args:
            error: Current error message to find fixes for.
            top_k: Number of fixes to retrieve.

        Returns:
            List of dicts with keys:
            - fix: Description of fix
            - agent: Agent that discovered it
            - task_id: Task where fix was found
            - similarity_score: How similar the error was
            - error_type: Classified error type
        """
        try:
            await struct_logger.ainfo(
                "fix_retrieval_started",
                error_sample=error[:100],
                top_k=top_k,
            )

            # Query collection
            results = self.collection.query(
                query_texts=[error],
                n_results=top_k,
                include=["metadatas", "distances"],
            )

            if not results or not results["metadatas"] or not results["metadatas"][0]:
                return []

            # Format results
            fixes = []
            for metadata, distance in zip(results["metadatas"][0], results["distances"][0]):
                similarity = 1 - distance if distance is not None else 0

                fixes.append(
                    {
                        "fix": metadata.get("fix", ""),
                        "agent": metadata.get("agent", "unknown"),
                        "task_id": metadata.get("task_id", ""),
                        "similarity_score": similarity,
                        "error_type": metadata.get("error_type", "unknown"),
                    }
                )

            await struct_logger.ainfo(
                "fix_retrieval_completed",
                results=len(fixes),
                top_similarity=fixes[0]["similarity_score"] if fixes else 0,
            )

            return fixes

        except Exception as e:
            logger.error(
                "Fix retrieval failed",
                error=str(e),
            )
            return []

    def _classify_error(self, error: str) -> str:
        """Classify error type by pattern matching.

        Args:
            error: Error message.

        Returns:
            Error type classification.
        """
        error_lower = error.lower()

        # Pattern-based classification
        if "typeerror" in error_lower or "type" in error_lower:
            return "type_error"
        elif "referenceerror" in error_lower or "undefined" in error_lower:
            return "reference_error"
        elif "syntaxerror" in error_lower or "syntax" in error_lower:
            return "syntax_error"
        elif "importerror" in error_lower or "modulenotfound" in error_lower:
            return "import_error"
        elif "timeout" in error_lower:
            return "timeout_error"
        elif "network" in error_lower or "connection" in error_lower:
            return "network_error"
        elif "assertion" in error_lower:
            return "assertion_error"
        elif "indexerror" in error_lower or "keyerror" in error_lower:
            return "access_error"
        else:
            return "unknown_error"

    async def get_stats(self) -> dict:
        """Get statistics about long-term memory.

        Returns:
            Dict with: total_fixes, error_types (dict), recent_tasks
        """
        try:
            all_items = self.collection.get(include=["metadatas"])

            if not all_items or not all_items["metadatas"]:
                return {
                    "total_fixes": 0,
                    "error_types": {},
                    "recent_tasks": [],
                }

            # Count by error type
            error_types = {}
            tasks = set()

            for metadata in all_items["metadatas"]:
                error_type = metadata.get("error_type", "unknown")
                error_types[error_type] = error_types.get(error_type, 0) + 1
                tasks.add(metadata.get("task_id", ""))

            return {
                "total_fixes": len(all_items["metadatas"]),
                "error_types": error_types,
                "unique_tasks": len(tasks),
            }

        except Exception as e:
            logger.warning("Failed to get long-term memory stats", error=str(e))
            return {}

    async def clear_learning(self) -> None:
        """Clear all learned fixes (use with caution).

        Useful for testing or resetting learning data.
        """
        try:
            self.client.delete_collection(self.COLLECTION_NAME)
            self.collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.warning("Long-term memory cleared")
        except Exception as e:
            logger.error("Failed to clear long-term memory", error=str(e))


# Global long-term memory instance
_long_term_memory_instance: Optional[LongTermMemory] = None


async def get_long_term_memory() -> LongTermMemory:
    """Get or create global long-term memory instance.

    Returns:
        LongTermMemory instance.
    """
    global _long_term_memory_instance
    if _long_term_memory_instance is None:
        _long_term_memory_instance = LongTermMemory()
    return _long_term_memory_instance
