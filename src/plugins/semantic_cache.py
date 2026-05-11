import os
import uuid
import time
import re
from qdrant_client import QdrantClient, models
from fastembed import TextEmbedding
from loguru import logger

# Single source of truth — never let Qdrant infer the name
VECTOR_NAME = "query_vector"

class SemanticCache:
    def __init__(self, collection_name: str = "sera_semantic_cache", threshold: float = 0.88):
        self.threshold = threshold
        self.collection_name = collection_name

        logger.info("Loading embedding model...")
        self.embedder = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")

        if qdrant_url and qdrant_api_key:
            self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
            logger.info("Connected to Qdrant Cloud successfully.")
        else:
            logger.warning("No Qdrant credentials found! Falling back to In-Memory DB.")
            self.client = QdrantClient(":memory:")

        # Compute vector size once from a real embed call
        self.vector_size = len(next(self.embedder.embed(["test"])).tolist())

        self._ensure_collection()

    def _ensure_collection(self):
        """
        Guarantee the collection exists with the exact vector config we expect.
        If it exists but was created by an old version (wrong name / wrong size),
        drop it and recreate — the cache is warm-up data, not mission-critical state.
        """
        needs_recreate = False

        if self.client.collection_exists(self.collection_name):
            try:
                info = self.client.get_collection(self.collection_name)
                vectors_cfg = info.config.params.vectors

                if isinstance(vectors_cfg, dict):
                    # Named-vector collection
                    existing = vectors_cfg.get(VECTOR_NAME)
                    if existing is None or existing.size != self.vector_size:
                        logger.warning(
                            f"Collection '{self.collection_name}' has wrong vector config "
                            f"(expected named '{VECTOR_NAME}' of size {self.vector_size}). Recreating..."
                        )
                        needs_recreate = True
                else:
                    # Unnamed-vector collection (old style) — always recreate
                    logger.warning(
                        f"Collection '{self.collection_name}' uses unnamed vectors. Recreating..."
                    )
                    needs_recreate = True

            except Exception as e:
                logger.warning(f"Could not inspect collection config ({e}). Recreating...")
                needs_recreate = True

            if needs_recreate:
                self.client.delete_collection(self.collection_name)

        if needs_recreate or not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    VECTOR_NAME: models.VectorParams(
                        size=self.vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
            )
            logger.info(
                f"Collection '{self.collection_name}' created "
                f"(vector='{VECTOR_NAME}', size={self.vector_size})."
            )
        else:
            logger.info(f"Collection '{self.collection_name}' config verified. Ready.")

    def _embed(self, text: str) -> list[float]:
        return next(self.embedder.embed([text])).tolist()

    def clean_query(self, query: str) -> str:
        return re.sub(r'\s+', ' ', query).strip()

    def get_cached_response(self, query: str) -> str | None:
        try:
            vector = self._embed(self.clean_query(query))

            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                using=VECTOR_NAME,          # ← explicit: never rely on Qdrant defaults
                limit=1,
            )

            if results.points:
                best = results.points[0]
                if best.score >= self.threshold:
                    logger.info(
                        f"Cache HIT  | score={best.score:.3f} | query='{query[:60]}'"
                    )
                    return best.payload.get("response")

            logger.info(f"Cache MISS | query='{query[:60]}'")
            return None

        except Exception as e:
            logger.error(f"Qdrant read error: {e}")
            return None

    def add_to_cache(self, query: str, response: str):
        try:
            cleaned = self.clean_query(query)
            vector = self._embed(cleaned)
            point_id = str(uuid.uuid4())

            self.client.upsert(                 # ← no deprecated .add()
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector={VECTOR_NAME: vector},   # ← explicit named vector
                        payload={
                            "original_query": cleaned,
                            "response": response,
                            "timestamp": time.time(),
                            "source": "sera_fastmcp_backend",
                        },
                    )
                ],
            )
            logger.info(f"Cache saved (ID: {point_id}) | query='{cleaned[:60]}'")

        except Exception as e:
            logger.error(f"Qdrant write error: {e}")


sera_cache = SemanticCache()