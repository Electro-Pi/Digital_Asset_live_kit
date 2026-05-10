import os
from google import genai
from qdrant_client import QdrantClient
from livekit.agents import function_tool, ToolError

from dotenv import load_dotenv

load_dotenv(".env.local")

COLLECTION_NAME = "sera_knowledge_base"

# Lazy initialization of clients
_genai_client = None
_qdrant_client = None

def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _genai_client

def _get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            url="https://4c4a2430-c49d-4010-b4fe-d51a671b2c3b.eu-central-1-0.aws.cloud.qdrant.io", 
            api_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6MTQzMGFiMTItYjMwNS00NTIxLTkxN2UtYTE2ZWNhMDI1MTk2In0.WFMOUl1DQ-BN_7mmhMCfZreMZyNQYYwkEq9c2uFYdO0"
        )
    return _qdrant_client

# Your Tool Definition
@function_tool
async def search_sera_knowledge_base(query: str) -> str:
    """
    Search SERA knowledge base for electricity regulations, complaints, and billing info.
    Use this tool whenever the question requires factual or official information.
    """
    try:
        genai_client = _get_genai_client()
        qdrant_client = _get_qdrant_client()
        
        # Embedding
        result = genai_client.models.embed_content(
            model="gemini-embedding-2-preview",
            contents=query,
            config={"output_dimensionality": 768}
        )

        query_vector = result.embeddings[0].values

        # Search
        search_result = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            using="dense",
            limit=3,
            with_payload=True
        )

        # Format results
        chunks = [
            point.payload.get("content", "")
            for point in search_result.points
        ]

        if not chunks:
            return "لا توجد معلومات متاحة في قاعدة البيانات."

        formatted = "\n\n".join(
            [f"نتيجة {i+1}:\n{text}" for i, text in enumerate(chunks)]
        )

        return formatted

    except Exception as e:
        raise ToolError(f"RAG search failed: {str(e)}")