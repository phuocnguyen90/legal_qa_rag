# src/model/query_model.py

import os
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, ClassVar
import time
from hashlib import md5
import json
from pathlib import Path
import uuid
from shared_libs.utils.logger import Logger
import aiofiles
import asyncio
from functools import partial
from concurrent.futures import ThreadPoolExecutor

# Local file setup for development mode
LOCAL_STORAGE_DIR = Path("local_data")
LOCAL_STORAGE_DIR.mkdir(exist_ok=True)
LOCAL_QUERY_FILE = LOCAL_STORAGE_DIR / "query_data.json"

# Initialize logger
logger = Logger.get_logger(module_name=__name__)




class QueryModel(BaseModel):
    query_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    cache_key: str = Field(default_factory=lambda: None)
    create_time: int = Field(default_factory=lambda: int(time.time()))
    query_text: str
    answer_text: Optional[str] = None
    sources: List[str] = Field(default_factory=list)
    is_complete: bool = False
    timestamp: Optional[int] = None
    conversation_history: Optional[List[Dict[str, str]]] = Field(default_factory=list)  # If applicable

    # Class-level DynamoDB clients (not model fields)
    dynamodb_client: ClassVar[Optional[boto3.client]] = None
    dynamodb_resource: ClassVar[Optional[boto3.resource]] = None

    # Class-level ThreadPoolExecutor (not a model field)
    executor: ClassVar[ThreadPoolExecutor] = ThreadPoolExecutor(max_workers=3)

    class Config:
        arbitrary_types_allowed = True  # Allow boto3 clients and executors as arbitrary types

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.cache_key:
            self.cache_key = self._generate_cache_key(self.query_text)

    @staticmethod
    def _generate_cache_key(query_text: str) -> str:
        """Generate a consistent cache key by hashing the normalized query text."""
        normalized_key = query_text.strip().lower()
        return md5(normalized_key.encode('utf-8')).hexdigest()

    @classmethod
    def initialize_dynamodb(cls):
        """
        Initialize the DynamoDB client and resource as class variables.
        This method should be called before any DynamoDB operations.
        """
        if cls.dynamodb_client is None and cls.dynamodb_resource is None:
            DEVELOPMENT_MODE = os.getenv("DEVELOPMENT_MODE", "False") == "True"
            AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
            if not DEVELOPMENT_MODE:
                try:
                    cls.dynamodb_client = boto3.client('dynamodb', region_name=AWS_REGION)
                    cls.dynamodb_resource = boto3.resource('dynamodb', region_name=AWS_REGION)
                    logger.info("Connected to DynamoDB in QueryModel.")
                except ClientError as e:
                    logger.error(f"Failed to initialize DynamoDB client: {e.response['Error']['Message']}")
                except Exception as e:
                    logger.error(f"Unexpected error initializing DynamoDB client: {str(e)}")



    @classmethod
    async def get_item(cls, query_id: str) -> Optional['QueryModel']:
        cls.initialize_dynamodb()
        if cls.dynamodb_resource is None:
            logger.error("DynamoDB resource is not initialized.")
            return None

        cache_table_name = os.getenv("CACHE_TABLE_NAME", "CacheTable")
        table = cls.dynamodb_resource.Table(cache_table_name)
        logger.debug(f"Querying DynamoDB Table: {cache_table_name} with query_id: {query_id}")
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                cls.executor,
                partial(
                    table.query,
                    KeyConditionExpression=Key('query_id').eq(query_id),
                    Limit=1  # Optional: if you expect only one item per query_id
                )
            )
            items = response.get('Items', [])
            if items:
                item = items[0]
                logger.debug(f"Item retrieved successfully from DynamoDB for query_id: {query_id}")
                return cls(**item)
            else:
                logger.warning(f"No item found in DynamoDB for query_id: {query_id}")
                return None
        except Exception as e:
            logger.error(f"Error retrieving item from DynamoDB: {str(e)}")
            return None

    
    @classmethod
    async def get_item_by_cache_key(cls, cache_key: str) -> Optional['QueryModel']:
        cls.initialize_dynamodb()
        if cls.dynamodb_resource is None:
            logger.error("DynamoDB resource is not initialized.")
            return None

        cache_table_name = os.getenv("CACHE_TABLE_NAME", "CacheTable")
        table = cls.dynamodb_resource.Table(cache_table_name)
        gsi_name = 'cache_key-index'

        logger.debug(f"Querying DynamoDB Table: {cache_table_name} using GSI: {gsi_name} for cache_key: {cache_key}")
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                cls.executor,
                partial(
                    table.query,
                    IndexName=gsi_name,
                    KeyConditionExpression=Key('cache_key').eq(cache_key),
                    Limit=1
                )
            )
            items = response.get('Items', [])
            if items:
                item = items[0]
                logger.debug(f"Item retrieved successfully from DynamoDB for cache_key: {cache_key}")
                return cls(**item)
            else:
                logger.warning(f"No item found in DynamoDB for cache_key: {cache_key}")
                return None
        except ClientError as e:
            error_message = e.response['Error']['Message']
            if "backfilling" in error_message.lower():
                logger.warning(f"GSI is backfilling, skipping cache check for cache_key: {cache_key}")
                return None
            else:
                logger.error(f"Failed to query DynamoDB by cache_key: {error_message}")
                return None
        except Exception as e:
            logger.error(f"Unexpected error during get_item_by_cache_key operation: {str(e)}")
            return None



    async def put_item(self):
        """
        Asynchronously put the QueryModel item into DynamoDB.
        """
        if self.dynamodb_resource is None:
            self.initialize_dynamodb()
            if self.dynamodb_resource is None:
                logger.error("DynamoDB resource is not initialized.")
                return

        cache_table_name = os.getenv("CACHE_TABLE_NAME", "CacheTable")
        table = self.dynamodb_resource.Table(cache_table_name)
        logger.debug(f"Putting item into DynamoDB Table: {cache_table_name} for query_id: {self.query_id}")
        try:
            # Convert the QueryModel to a DynamoDB-compatible dict
            item = self.as_ddb_item()

            await asyncio.get_event_loop().run_in_executor(
                self.executor,
                partial(
                    table.put_item,
                    Item=item,
                    ConditionExpression='attribute_not_exists(query_id)'
                )
            )
            logger.debug(f"Item put successfully into DynamoDB for query_id: {self.query_id}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"Conditional check failed for query_id: {self.query_id}. Possibly already processed.")
            else:
                logger.error(f"Error putting item into DynamoDB: {str(e)}")
                raise
        except Exception as e:
            logger.error(f"Error putting item into DynamoDB: {str(e)}")
            raise

    async def update_item(self, query_id: str, query_item: 'QueryModel') -> bool:
        """
        Update an existing DynamoDB record identified by query_id by copying non-None values from query_item.

        Args:
            query_id (str): The unique identifier for the query to update.
            query_item (QueryModel): The QueryModel instance containing fields to update.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        # Ensure DynamoDB is initialized
        if self.dynamodb_resource is None:
            self.initialize_dynamodb()
            if self.dynamodb_resource is None:
                logger.error("DynamoDB resource is not initialized.")
                return False

        cache_table_name = os.getenv("CACHE_TABLE_NAME", "CacheTable")
        table = self.dynamodb_resource.Table(cache_table_name)

        # Convert query_item to a dictionary, excluding unset fields
        update_fields = query_item.dict(exclude_unset=True)

        if not update_fields:
            logger.warning("No fields provided to update.")
            return False

        # Build the UpdateExpression dynamically based on the fields to update
        update_expression = "SET " + ", ".join([f"{k} = :{k}" for k in update_fields.keys()])

        # Prepare the ExpressionAttributeValues
        expression_attribute_values = {f":{k}": v for k, v in update_fields.items()}

        logger.debug(f"Updating item in DynamoDB Table: {cache_table_name} for query_id: {query_id}")
        logger.debug(f"UpdateExpression: {update_expression}")
        logger.debug(f"ExpressionAttributeValues: {expression_attribute_values}")

        try:
            await asyncio.get_event_loop().run_in_executor(
                self.executor,
                partial(
                    table.update_item,
                    Key={'query_id': query_id},
                    UpdateExpression=update_expression,
                    ExpressionAttributeValues=expression_attribute_values,
                    ReturnValues="UPDATED_NEW"  # Returns the updated attributes
                )
            )
            logger.info(f"Successfully updated item in DynamoDB for query_id: {query_id}")
            return True
        except ClientError as e:
            logger.error(f"Failed to update item in DynamoDB: {e.response['Error']['Message']}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during update_item: {str(e)}")
            return False


    async def save_to_local(self):
        """Asynchronously save the current query model to a local JSON file."""
        try:
            logger.debug(f"Saving query data locally for query_id: {self.query_id}")
            data = []
            if LOCAL_QUERY_FILE.exists():
                async with aiofiles.open(LOCAL_QUERY_FILE, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)

            # Update or append the entry
            found = False
            for idx, item in enumerate(data):
                if item.get("query_id") == self.query_id:
                    data[idx] = self.dict()
                    found = True
                    break

            if not found:
                data.append(self.dict())

            async with aiofiles.open(LOCAL_QUERY_FILE, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2))

            logger.debug(f"Query data saved locally: {self.query_id}")
        except Exception as e:
            logger.error(f"Failed to save query locally: {str(e)}", {"query_id": self.query_id})


    @classmethod
    async def load_from_local(cls, query_id: str) -> Optional['QueryModel']:
        """
        Asynchronously load a specific QueryModel from the local JSON file by query_id.

        Args:
            query_id (str): The unique identifier for the query.

        Returns:
            Optional[QueryModel]: The retrieved QueryModel instance or None if not found.
        """
        try:
            logger.debug(f"Loading query from local storage for query_id: {query_id}")
            if LOCAL_QUERY_FILE.exists():
                async with aiofiles.open(LOCAL_QUERY_FILE, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    for item in data:
                        if item.get("query_id") == query_id:
                            logger.info(f"Query data loaded from local storage for query_id: {query_id}")
                            return cls(**item)
            logger.warning(f"No local data found for query_id: {query_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to load query locally: {str(e)}", {"query_id": query_id})
            return None

    @classmethod
    async def load_from_local_by_cache_key(cls, cache_key: str) -> Optional['QueryModel']:
        """
        Asynchronously load a specific QueryModel from the local JSON file by cache_key.

        Args:
            cache_key (str): The cache key derived from the query text.

        Returns:
            Optional[QueryModel]: The retrieved QueryModel instance or None if not found.
        """
        try:
            logger.debug(f"Loading query from local storage for cache_key: {cache_key}")
            if LOCAL_QUERY_FILE.exists():
                async with aiofiles.open(LOCAL_QUERY_FILE, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    for item in data:
                        if item.get("cache_key") == cache_key:
                            logger.info(f"Query data loaded from local storage for cache_key: {cache_key}")
                            return cls(**item)
            logger.warning(f"No local data found for cache_key: {cache_key}")
            return None
        except Exception as e:
            logger.error(f"Failed to load query locally: {str(e)}", {"cache_key": cache_key})
            return None

    def as_ddb_item(self) -> Dict[str, any]:
        """
        Convert the QueryModel instance to a DynamoDB-compatible dictionary.
        """
        logger.debug(f"Converting QueryModel to DynamoDB item format for query_id: {self.query_id}")
        # Prepare the item dict without type annotations
        item = {
            'query_id': self.query_id,
            'cache_key': self.cache_key,
            'create_time': self.create_time,
            'query_text': self.query_text,
            'answer_text': self.answer_text if self.answer_text else "",
            'sources': self.sources,
            'is_complete': self.is_complete,
            'timestamp': self.timestamp if self.timestamp else 0,
            'conversation_history': self.conversation_history if self.conversation_history else []
        }
        return item


    @staticmethod
    def _convert_conversation_history(history_item: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """
        Convert a single conversation history item to DynamoDB-compatible format.

        Args:
            history_item (Dict[str, str]): A single conversation history entry.

        Returns:
            Dict[str, Dict[str, str]]: The DynamoDB-compatible history entry.
        """
        return {k: {'S': v} for k, v in history_item.items()}

    @classmethod

    def convert_dynamodb_item(cls, item: Dict[str, any]) -> Dict[str, any]:
        """
        Convert a DynamoDB item to a regular Python dictionary.
        """
        return item  # Since boto3 returns a dict of native Python types
