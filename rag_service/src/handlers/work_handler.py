import os
from shared_libs.config.config_loader import ConfigLoader
from shared_libs.utils.logger import Logger
from shared_libs.utils.cache import Cache
from shared_libs.utils.provider_utils import load_llm_provider
import sys
import json
import asyncio
import boto3
from functools import partial
# Add parent directory to the sys.path to access shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.query_model import QueryModel
from services.query_rag import query_rag

# Initialize the logger
logger = Logger().get_logger(module_name=__name__)

# Load configuration and LLM provider
config = ConfigLoader()
llm_provider = load_llm_provider()

# Cache TTL for responses
# CACHE_TTL = 1800  # 30 minutes
# Worker configuration
POLL_INTERVAL = 10  # seconds
MAX_MESSAGES = 10 
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL")

# Initialize boto3 clients
sqs_client = boto3.client('sqs', region_name=AWS_REGION)

async def handler(event, context):
    for record in event['Records']:
        try:
            payload = json.loads(record['body'])
            query_id = payload.get('query_id')
            if not query_id:
                logger.error("No query_id found in payload.")
                continue  # Skip processing if no query_id

            query_item = await QueryModel.get_item(query_id)
            if not query_item:
                logger.error(f"No query found in database for query_id: {query_id}")
                continue

            # Invoke RAG processing with cache handling
            await query_rag(query_item, provider=llm_provider)

            # Update the query item in DynamoDB
            await query_item.put_item()

        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            # Optionally, handle retries or dead-letter queue

async def process_message(message):
    """
    Process a single SQS message.
    """
    try:
        # Deserialize message
        body = json.loads(message['Body'])
        query_id = body['query_id']
        logger.info(f"Received message for query_id: {query_id}")
        query_text = body['query_text']
        logger.info(f"Processing query: {query_text}")

        # Retrieve the query from DynamoDB
        query_item = await QueryModel.get_item(query_id)
        if not query_item:
            logger.error(f"Query item not found for query_id: {query_id}")
            return

        # Perform RAG to generate response
        response = await query_rag(query_item, provider=None)  # Pass the query_item directly

        # Update the query item with the response
        query_item.answer_text = response.response_text
        query_item.query_text=query_text
        query_item.sources = response.sources
        query_item.is_complete = True
        query_item.timestamp = response.timestamp  # Assuming timestamp is part of QueryResponse

        # Save the updated query_item to DynamoDB
        await query_item.put_item()

        # Delete the message from the queue after successful processing
        await delete_message(message['ReceiptHandle'])
        logger.info(f"Successfully processed and deleted message for query_id: {query_id}")

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        # Depending on requirements, you can decide to delete the message or leave it for retry
        # For now, we'll leave it to be retried
        pass

async def delete_message(receipt_handle):
    """
    Delete a message from the SQS queue.
    """
    loop = asyncio.get_event_loop()
    delete_partial = partial(
        sqs_client.delete_message,
        QueueUrl=SQS_QUEUE_URL,
        ReceiptHandle=receipt_handle
    )
    await loop.run_in_executor(
        None,
        delete_partial
    )

async def poll_queue():
    """
    Continuously poll the SQS queue for new messages.
    """
    while True:
        try:
            # Receive messages with long polling
            receive_partial = partial(
                sqs_client.receive_message,
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=20  # Enable long polling
            )
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                receive_partial
            )

            messages = response.get('Messages', [])
            if not messages:
                logger.debug("No messages received.")
            else:
                logger.info(f"Received {len(messages)} messages.")

                # Process each message concurrently
                tasks = [process_message(message) for message in messages]
                await asyncio.gather(*tasks)

        except Exception as e:
            logger.error(f"Error polling SQS queue: {str(e)}")

        # Wait before next poll to avoid tight loop
        await asyncio.sleep(POLL_INTERVAL)

# For local testing
# async def main():
    #"""
    #For local testing.
    #"""
    #logger.info("Running example RAG call.")
    #query_item = QueryModel(
    #    query_text="làm sao để kinh doanh vũ trường?"
    #)
    
    # Since query_rag is an async function, we need to await its result.
    #response = await query_rag(query_item)
    #print(f"Received: {response}")

# if __name__ == "__main__":
    # For local testing: use asyncio.run to execute the async main function.
    # asyncio.run(main())
def main():
    """
    Entry point for the worker handler.
    """
    logger.info("Worker handler started. Polling SQS queue...")
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(poll_queue())
    except KeyboardInterrupt:
        logger.info("Worker handler stopped manually.")
    finally:
        loop.close()

if __name__ == "__main__":
    main()