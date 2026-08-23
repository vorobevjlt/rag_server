import boto3
from botocore.config import Config
from src.config.index import appConfig

s3_client = boto3.client(
    "s3",
    aws_access_key_id=appConfig["aws_access_key_id"],
    aws_secret_access_key=appConfig["aws_secret_access_key"],
    region_name=appConfig["aws_region"],
    endpoint_url=appConfig["s3_endpoint_url"] or None,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": appConfig["s3_addressing_style"]},
    ),
)
