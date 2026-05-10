import os
import chromadb
from chromadb.config import Settings
from loguru import logger

class SemanticCache:
    def __init__(self, cache_dir: str = "./cache_db", threshold: float = 0.3):
        self.threshold = threshold
        
        os.makedirs(cache_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=cache_dir)
        
        self.collection = self.client.get_or_create_collection(
            name="sera_semantic_cache",
            metadata={"hnsw:space": "cosine"}
        )

    def get_cached_response(self, query: str) -> str | None:
        """Search the vector database for a semantically similar query."""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=1
            )
            
            if results["distances"] and results["distances"][0]:
                distance = results["distances"][0][0]
                if distance < self.threshold:
                    logger.info(f"Semantic Cache HIT! Distance: {distance:.3f}")
                    return results["documents"][0][0]
            
            logger.info("Semantic Cache MISS.")
            return None
            
        except Exception as e:
            logger.error(f"Cache read error: {e}")
            return None

    def add_to_cache(self, query: str, response: str):
        """Add a new query and its response to the vector database."""
        try:
            # Generate a simple ID based on the count
            doc_id = f"q_{self.collection.count() + 1}"
            
            self.collection.add(
                documents=[response],
                metadatas=[{"original_query": query}],
                ids=[doc_id]
            )
            logger.info(f"Added new response to Semantic Cache (ID: {doc_id})")
        except Exception as e:
            logger.error(f"Cache write error: {e}")

sera_cache = SemanticCache()