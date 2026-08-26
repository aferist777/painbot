"""Cloudflare R2 upload. Lifts the 50 MB ceiling Telegram puts on bot uploads.

The bucket is shared with another project, so everything lives under a prefix.
Without a public domain the link is presigned; seven days is the S3 maximum.
"""
import logging
import mimetypes
from pathlib import Path
from typing import Optional

from app import config

log = logging.getLogger("painbot.storage")

LINK_TTL = 7 * 24 * 3600  # S3 signature maximum
_client = None


def ready() -> bool:
    return config.R2_READY


def client():
    global _client
    if _client is not None:
        return _client
    if not ready():
        raise RuntimeError("R2 не настроен — заполни R2_* в painbot/.env")
    import boto3
    from botocore.config import Config

    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )
    return _client


def key_for(name: str) -> str:
    return (config.R2_PREFIX or "") + name.lstrip("/")


def upload(path: Path, name: str) -> dict[str, str]:
    key = key_for(name)
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    client().upload_file(
        str(path), config.R2_BUCKET, key, ExtraArgs={"ContentType": content_type}
    )
    log.info("uploaded %s -> %s", path.name, key)
    return {"key": key, "url": link(key)}


def link(key: str) -> str:
    if config.R2_PUBLIC_BASE:
        return f"{config.R2_PUBLIC_BASE}/{key}"
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.R2_BUCKET, "Key": key},
        ExpiresIn=LINK_TTL,
    )


def delete(key: str) -> None:
    client().delete_object(Bucket=config.R2_BUCKET, Key=key)
