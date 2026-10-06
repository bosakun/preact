import hashlib
import json
import os

from preact.core.store import Artifacts


class S3Artifacts:
    def __init__(self, bucket, endpoint=None, client=None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=endpoint,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        )

    def put(self, data: bytes, media="application/json"):
        digest = hashlib.sha256(data).hexdigest()
        self.client.put_object(
            Bucket=self.bucket,
            Key=digest,
            Body=data,
            ContentType=media,
            Metadata={"sha256": digest},
        )
        return digest

    def json(self, value):
        return self.put(json.dumps(value, sort_keys=True).encode())

    def read(self, digest):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid artifact digest")
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=digest)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(digest) from None
            raise
        # Release pooled HTTP connections even when checksum validation fails.
        with response["Body"] as body:
            data = body.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Artifact integrity failure")
        return data


def artifact_store():
    if os.getenv("S3_BUCKET"):
        return S3Artifacts(os.environ["S3_BUCKET"], os.getenv("S3_ENDPOINT"))
    return Artifacts(os.getenv("PREACT_ARTIFACTS_DIR", ".preact/artifacts"))
