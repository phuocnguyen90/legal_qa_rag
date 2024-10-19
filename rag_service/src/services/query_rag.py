# src/services/query_rag.py

from dataclasses import dataclass
from typing import List, Optional
import logging
import re
import time

# Imports from shared_libs
from shared_libs.providers import ProviderFactory  # Use the provider factory to dynamically get providers
from shared_libs.utils.logger import Logger
from shared_libs.utils.cache import Cache
from shared_libs.config.config_loader import ConfigLoader

from search_qdrant import search_qdrant  

# Load configuration
config_loader = ConfigLoader()  # Load config once globally

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load the RAG prompt from config
rag_prompt = config_loader.get_prompt("rag_prompt")

# Load the default provider using ProviderFactory, including fallback logic
provider_name = config_loader.get_config_value("provider", "groq")
llm_settings = config_loader.get_config_value(provider_name, {})
requirements = config_loader.get_config_value("requirements", "")
llm_provider = ProviderFactory.get_provider(name=provider_name, config=llm_settings, requirements=requirements)

@dataclass
class QueryResponse:
    query_text: str
    response_text: str
    sources: List[str]

def validate_citation(response: str) -> bool:
    """
    Validate that the response contains at least one Document ID citation.

    :param response: The response generated by the LLM.
    :return: True if at least one citation is found, False otherwise.
    """
    pattern = r'\[Mã tài liệu:\s*[\w-]+\]'
    return bool(re.search(pattern, response))

def query_rag(query_text: str, provider=None, conversation_history: Optional[List[str]] = None) -> QueryResponse:
    """
    Perform Retrieval-Augmented Generation (RAG) to answer the user's query.

    :param query_text: The user's input question.
    :param provider: An instance of an LLM provider to interact with the LLM.
    :param conversation_history: A list of previous conversation messages for multi-turn interaction.
    :return: A QueryResponse containing the answer and sources.
    """
    if provider is None:
        provider = llm_provider

    # Step 1: Check Cache for an Existing Response
    logger.info(f"Checking cache for query: {query_text}")
    cached_response = Cache.get(query_text)
    if cached_response:
        logger.info(f"Cache hit for query: {query_text}")
        return QueryResponse(
            query_text=cached_response["query_text"],
            response_text=cached_response["response_text"],
            sources=cached_response["sources"]
        )

    # Step 2: Retrieve similar documents using Qdrant
    logger.info(f"Retrieving documents related to query: {query_text}")
    retrieved_docs = search_qdrant(query_text, top_k=3)
    if not retrieved_docs:
        logger.warning(f"No relevant documents found for query: {query_text}")
        return QueryResponse(
            query_text=query_text,
            response_text="Không tìm thấy thông tin liên quan.",
            sources=[]
        )

    # Step 3: Combine retrieved documents to form context for LLM
    context = "\n\n------------------------------------------------------\n\n".join([
        f"Mã tài liệu: {doc['record_id']}\nNguồn: {doc['source']}\nNội dung: {doc['content']}"
        for doc in retrieved_docs if doc['content']
    ])
    logger.info(f"Retrieved documents combined to form context for query: {query_text}")

    # Step 4: Define the system prompt with clear citation instructions using the loaded prompt
    system_prompt = rag_prompt
    logger.debug(f"System prompt loaded for query: {query_text}")

    # Step 5: Combine system prompt and user message to form the full prompt
    full_prompt = f"{system_prompt}\n\nCâu hỏi của người dùng: {query_text}\n\nCác câu trả lời liên quan:\n\n{context}"
    logger.info(f"Full prompt created for query: {query_text}")

    # Step 6: Send the prompt to the LLM via the provided provider
    try:
        logger.info(f"Sending prompt to LLM for query: {query_text}")

        # Differentiate between single-message and multi-turn interaction
        if conversation_history:
            response_text = provider.send_multi_turn_message(prompt=full_prompt, conversation_history=conversation_history)
        else:
            response_text = provider.send_single_message(prompt=full_prompt)

        if not response_text:
            logger.error(f"No response generated by LLM for query: {query_text}")
            response_text = "Đã xảy ra lỗi khi tạo câu trả lời."
    except Exception as e:
        logger.error(f"Failed to generate a response using the provider for query: {query_text}. Error: {str(e)}")
        response_text = "Đã xảy ra lỗi khi tạo câu trả lời."

    # Step 7: Validate that the answer contains at least one citation
    if not validate_citation(response_text):
        logger.warning(f"The response for query '{query_text}' does not contain any citations from the documents.")
    else:
        logger.info(f"The response for query '{query_text}' contains citations from the documents.")

    # Step 8: Extract sources from retrieved_docs
    sources = [doc['record_id'] for doc in retrieved_docs]

    # Step 9: Cache the response for future queries
    cache_data = {
        "query_text": query_text,
        "response_text": response_text,
        "sources": sources,
        "timestamp": int(time.time())  # Adding timestamp for potential TTL handling
    }
    Cache.set(query_text, cache_data)
    logger.info(f"Cached response for query: {query_text}")

    return QueryResponse(
        query_text=query_text,
        response_text=response_text,
        sources=sources
    )

if __name__ == "__main__":
    # Example usage
    user_query = "Thủ tục chia thừa kế như thế nào?"
    response = query_rag(user_query)
    print(f"Query: {response.query_text}")
    print(f"Answer: {response.response_text}")
    print(f"Sources: {response.sources}")
