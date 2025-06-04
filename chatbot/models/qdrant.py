from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from langchain_core.embeddings import Embeddings

import numpy as np

class QDrantDB:

    def __init__(
        self,
        host: str,
        port: Optional[int] = 6333,
        collection_name: str = "support",
        embeddings: Optional[Embeddings] = None,
        vector_size: int = 768,
        prefer_grpc: bool = True,
    ) -> None:
        self._validate_init_params(host, port, collection_name, vector_size)
        
        self.collection_name = collection_name
        self.embeddings = embeddings
        self.client = QdrantClient(host=host, port=port, prefer_grpc=prefer_grpc)
        
        self._ensure_collection_exists(vector_size)

    def _validate_init_params(
        self, host: str, port: Optional[int], collection_name: str, vector_size: int
    ) -> None:
        """Validate initialization parameters."""
        if not host:
            raise ValueError("Host cannot be empty")
        if port is not None and not (0 <= port <= 65535):
            raise ValueError("Port must be between 0 and 65535")
        if not collection_name:
            raise ValueError("Collection name cannot be empty")
        if vector_size <= 0:
            raise ValueError("Vector size must be positive")

    def _ensure_collection_exists(self, vector_size: int) -> None:
        """Create collection if it doesn't exist."""
        collections = self.client.get_collections().collections
        collection_names = [collection.name for collection in collections]
        
        if self.collection_name not in collection_names:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            