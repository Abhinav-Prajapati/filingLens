"""S3-compatible object store for source filings (SeaweedFS in docker-compose.yml; any S3 API
works). Settings: FILINGLENS_S3_ENDPOINT, _BUCKET, _ACCESS_KEY, _SECRET_KEY.

Layout:  filings/<TICKER>/xbrl/<file>.xml
         filings/<TICKER>/annual_report/<file>.pdf

The bucket is the durable copy; ingestion reads a local working copy (the inbox), so parsing
never depends on the network.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filingLens.config import setting


@dataclass
class StoredFile:
    key: str
    size: int
    modified: str                    # ISO timestamp


def filing_key(ticker: str, kind: str, name: str) -> str:
    """kind: "xbrl" | "annual_report" (the inbox sub-folder)."""
    return f"filings/{ticker.upper()}/{kind}/{name}"


class ObjectStore:
    def __init__(self, client: Any, bucket: str):
        self.s3, self.bucket = client, bucket
        self._ready = False

    @classmethod
    def from_env(cls) -> ObjectStore | None:
        """None when no endpoint/keys are configured (the app then keeps files locally only)."""
        endpoint = setting("FILINGLENS_S3_ENDPOINT")
        key, secret = setting("FILINGLENS_S3_ACCESS_KEY"), setting("FILINGLENS_S3_SECRET_KEY")
        if not (endpoint and key and secret):
            return None
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id=key, aws_secret_access_key=secret,
            region_name=setting("FILINGLENS_S3_REGION", "us-east-1"),
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3},
                          connect_timeout=5, read_timeout=60))
        return cls(client, setting("FILINGLENS_S3_BUCKET", "filinglens"))

    def ensure_bucket(self) -> None:
        if self._ready:
            return
        names = {b["Name"] for b in self.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in names:
            self.s3.create_bucket(Bucket=self.bucket)
        self._ready = True

    def put_file(self, path: str | Path, key: str) -> str:
        """Upload ``path`` to ``key``; returns its s3:// URI."""
        self.ensure_bucket()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": ctype})
        return self.uri(key)

    def exists(self, key: str) -> bool:
        self.ensure_bucket()
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3.exceptions.ClientError:
            return False

    def list(self, prefix: str) -> list[StoredFile]:
        self.ensure_bucket()
        out: list[StoredFile] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket,
                                                                       Prefix=prefix):
            out += [StoredFile(o["Key"], o["Size"], o["LastModified"].isoformat(timespec="seconds"))
                    for o in page.get("Contents", [])]
        return out

    def download_url(self, key: str, seconds: int = 900) -> str:
        """A short-lived link the browser can open (the bucket itself stays private)."""
        return str(self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=seconds))

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"
