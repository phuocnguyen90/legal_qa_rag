# src/embeddings/local_embedder.py

from typing import List
from .base_embedder import BaseEmbedder
from shared_libs.config.embedding_config import LocalEmbeddingConfig
from shared_libs.utils.logger import Logger
import fastembed

logger = Logger.get_logger(module_name=__name__)

class LocalEmbedder(BaseEmbedder):
    def __init__(self, config: LocalEmbeddingConfig):
        """
        Initialize the LocalEmbedder with the specified configuration.

        :param config: LocalEmbeddingConfig instance containing necessary parameters.
        """
        self.model = fastembed.TextEmbedding(
            model_name=config.model_name, 
        )
        logger.info(f"LocalEmbedder initialized with model '{config.model_name}'.")

    def embed(self, text: str) -> List[float]:
        """
        Generate an embedding for a single text string.
        """
        try:
            embedding = self.model.embed(text)
            if not embedding:
                logger.error("No embedding generated by local model.")
                return []
            return embedding.tolist() if hasattr(embedding, 'tolist') else embedding
        except Exception as e:
            logger.error(f"Error during local embed: {e}")
            return []
        
    def batch_embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of input texts.
        """
        try:
            embeddings = self.model.embed(texts)
            if not embeddings:
                logger.error("No embeddings generated by local model for batch.")
                return [[] for _ in texts]
            return [embedding.tolist() if hasattr(embedding, 'tolist') else embedding for embedding in embeddings]
        except Exception as e:
            logger.error(f"Error during batch embed: {e}")
            return [[] for _ in texts]