# src/rag/query_rag.py

from dataclasses import dataclass
from typing import List
import logging
import re

from providers.groq_provider import GroqProvider  # Adjust import path as necessary
from rag.search_qdrant import search_qdrant  # Adjust import path based on your project structure

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

def query_rag(query_text: str, groq_provider: GroqProvider) -> QueryResponse:
    """
    Perform Retrieval-Augmented Generation (RAG) to answer the user's query.

    :param query_text: The user's input question.
    :param groq_provider: An instance of GroqProvider to interact with the LLM.
    :return: A QueryResponse containing the answer and sources.
    """
    # Step 1: Retrieve similar documents using Qdrant
    retrieved_docs = search_qdrant(query_text, top_k=3)
    if not retrieved_docs:
        return QueryResponse(
            query_text=query_text,
            response_text="Không tìm thấy thông tin liên quan.",
            sources=[]
        )

    # Step 2: Combine retrieved documents to form context for LLM
    context = "\n\n------------------------------------------------------\n\n".join([
        f"Mã tài liệu: {doc['record_id']}\nNguồn: {doc['source']}\nNội dung: {doc['content']}"
        for doc in retrieved_docs if doc['content']
    ])

    # Step 3: Define the system prompt with clear citation instructions
    system_prompt = '''
    Bạn là một trợ lý pháp lý chuyên nghiệp. Dựa trên câu hỏi của người dùng và các kết quả tìm kiếm liên quan từ cơ sở dữ liệu câu hỏi thường gặp của bạn, hãy trả lời câu hỏi và trích dẫn cơ sở pháp lý nếu có trong thông tin được cung cấp.
    Không thêm ý kiến cá nhân; hãy trả lời chi tiết nhất có thể chỉ sử dụng các kết quả tìm kiếm được cung cấp để trả lời.
    Khi trích dẫn nguồn, hãy tham chiếu đến Mã tài liệu (Record ID) được cung cấp trong ngữ cảnh theo định dạng: [Mã tài liệu: <record_id>].
    Ví dụ: "Theo quy định trong [Mã tài liệu: QA_750F0D91], ...".
    Luôn trả lời bằng tiếng Việt.
    '''

    # Step 4: Combine system prompt and user message to form the full prompt
    full_prompt = f"{system_prompt}\n\nCâu hỏi của người dùng: {query_text}\n\nCác câu trả lời liên quan:\n\n{context}"

    # Step 5: Send the prompt to the LLM via GroqProvider
    response_text = groq_provider.send_message(prompt=full_prompt)

    if not response_text:
        response_text = "Đã xảy ra lỗi khi tạo câu trả lời."

    # Step 6: Validate that the answer contains at least one citation
    if not validate_citation(response_text):
        logger.warning("Câu trả lời không chứa bất kỳ trích dẫn nào từ Mã tài liệu.")
        # Optionally, handle the missing citation (e.g., re-prompt, notify user)
    else:
        logger.info("Câu trả lời chứa các trích dẫn từ Mã tài liệu.")

    # Step 7: Extract sources from retrieved_docs
    sources = [doc['record_id'] for doc in retrieved_docs]

    return QueryResponse(
        query_text=query_text,
        response_text=response_text,
        sources=sources
    )

if __name__ == "__main__":
    # Example usage
    from providers.groq_provider import GroqProvider  # Ensure correct import path

    # Initialize GroqProvider with necessary configuration
    groq_config = {
        'api_key': 'your_groq_api_key',  # Replace with your actual API key
        'model_name': 'your_model_name',  # Replace with your desired model
        'embedding_model_name': 'your_embedding_model_name',  # Replace as needed
        'temperature': 0.7,
        'max_output_tokens': 4096
    }

    groq_provider = GroqProvider(config=groq_config, requirements='')

    user_query = "How much does a landing page cost to develop?"
    response = query_rag(user_query, groq_provider)
    print(f"Query: {response.query_text}")
    print(f"Answer: {response.response_text}")
    print(f"Sources: {response.sources}")
