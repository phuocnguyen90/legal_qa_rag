# shared_libs/utils/aws_auth_validation.py

import boto3
import os
import logging
from botocore.exceptions import ClientError

# AWS Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("CACHE_TABLE_NAME", "CacheTable")
LOG_TABLE_NAME = os.getenv("LOG_TABLE_NAME", "LogTable")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "legal-rag-qa")

# Initialize AWS resources

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    
s3 = boto3.client("s3", region_name=AWS_REGION)

def validate_dynamodb(table_name):
    """Ensure that the specified DynamoDB table exists."""
    try:
        table = dynamodb.Table(table_name)
        table.load()  # This will trigger a resource load and fail if the table does not exist
        logging.debug(f"DynamoDB table '{table_name}' already exists.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            print(f"DynamoDB table '{table_name}' not found. Creating a new one.")
            try:
                table = dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=[
                        {
                            'AttributeName': 'query_id',
                            'KeyType': 'HASH'  # Partition key
                        }
                    ],
                    AttributeDefinitions=[
                        { "AttributeName": "query_id", "AttributeType": "S" },
                        { "AttributeName": "cache_key", "AttributeType": "S" }
                    ],
                    GlobalSecondaryIndexes=[
                        {
                            'IndexName': 'cache_key-index',
                            'KeySchema': [
                                { 'AttributeName': 'cache_key', 'KeyType': 'HASH' }
                            ],
                            'Projection': { 'ProjectionType': 'ALL'
                            }
                        }
                    ],
                    BillingMode='PAY_PER_REQUEST'  # Use on-demand billing
                )
                table.wait_until_exists()  # Wait until the table is created
                logging.debug(f"DynamoDB table '{table_name}' created successfully.")
            except ClientError as create_error:
                logging.debug(f"Failed to create DynamoDB table '{table_name}': {create_error}")
        else:
            logging.debug(f"Unexpected error while accessing DynamoDB: {e}")


def validate_s3():
    """Ensure that the specified S3 bucket exists."""
    try:
        # Check if the bucket exists by attempting to access its location
        s3.head_bucket(Bucket=S3_BUCKET_NAME)
        logging.debug(f"S3 bucket '{S3_BUCKET_NAME}' already exists.")
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            logging.debug(f"S3 bucket '{S3_BUCKET_NAME}' not found. Creating a new one.")
            try:
                if AWS_REGION == 'us-east-1':
                    # No LocationConstraint required for us-east-1
                    s3.create_bucket(
                        Bucket=S3_BUCKET_NAME
                    )
                else:
                    # For other regions, specify the LocationConstraint
                    s3.create_bucket(
                        Bucket=S3_BUCKET_NAME,
                        CreateBucketConfiguration={
                            'LocationConstraint': AWS_REGION
                        }
                    )
                logging.debug(f"S3 bucket '{S3_BUCKET_NAME}' created successfully.")
            except ClientError as create_error:
                logging.debug(f"Failed to create S3 bucket '{S3_BUCKET_NAME}': {create_error}")
        else:
            logging.debug(f"Unexpected error while accessing S3 bucket: {e}")
