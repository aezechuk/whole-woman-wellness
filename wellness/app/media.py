"""Session replays and handouts, stored in a private S3 bucket.

- The bucket is never public. Signed-in participants get short-lived signed links,
  generated per page view; admins upload straight from the browser to S3 with a
  signed upload link, so large videos never pass through the app server.
- Videos play in the app only (no download button). Handouts can be downloaded.
- Everything in the bucket expires on Feb 28, 2027 (bucket lifecycle rule).
"""

from __future__ import annotations

import os
import re
import uuid

from flask import current_app
from sqlalchemy import select

from .extensions import db
from .models import SessionResource

VIDEO_TYPES = {".mp4": "video/mp4"}
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}
VIDEO_MAX = 5 * 1024 ** 3          # a single signed upload can be up to 5 GB
FILE_MAX = 100 * 1024 ** 2
PLAY_FOR = 4 * 3600                # long enough to watch a 90-minute replay with pauses
DOWNLOAD_FOR = 300
TITLE_MAX = 120


class MediaError(ValueError):
    pass


# ---- storage backends -------------------------------------------------------------

class S3Storage:
    def __init__(self, bucket: str, region: str):
        import boto3
        from botocore.config import Config
        self.bucket, self.region = bucket, region
        self._s3 = boto3.client("s3", region_name=region, config=Config(
            signature_version="s3v4", s3={"addressing_style": "virtual"}))

    @property
    def origin(self) -> str:
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    def upload_url(self, key: str, content_type: str) -> str:
        return self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=3 * 3600)

    def view_url(self, key: str, content_type: str, seconds: int, download_as: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key, "ResponseContentType": content_type}
        if download_as:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_as}"'
        return self._s3.generate_presigned_url("get_object", Params=params, ExpiresIn=seconds)

    def size(self, key: str) -> int | None:
        try:
            return self._s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except Exception:
            return None

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)


class MemoryStorage:
    """Tests and local development: nothing leaves the process."""
    origin = "https://media.test"

    def __init__(self):
        self.objects: dict[str, int] = {}

    def upload_url(self, key, content_type):
        return f"{self.origin}/{key}?upload=1"

    def view_url(self, key, content_type, seconds, download_as=None):
        return f"{self.origin}/{key}?expires={seconds}" + ("&download=1" if download_as else "")

    def size(self, key):
        return self.objects.get(key)

    def delete(self, key):
        self.objects.pop(key, None)


def storage():
    """None when no bucket is set up yet (the feature stays hidden)."""
    app = current_app._get_current_object()
    if "media_storage" not in app.extensions:
        backend = app.config.get("MEDIA_BACKEND")
        if backend == "memory":
            app.extensions["media_storage"] = MemoryStorage()
        elif app.config.get("MEDIA_BUCKET"):
            app.extensions["media_storage"] = S3Storage(app.config["MEDIA_BUCKET"],
                                                       os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
        else:
            app.extensions["media_storage"] = None
    return app.extensions["media_storage"]


# ---- resources ----------------------------------------------------------------------

def _safe_name(filename: str) -> str:
    base = os.path.basename(filename or "").strip()
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem).strip()[:80] or "file"
    return f"{stem}{ext.lower()}"


def start_upload(week_key: str, kind: str, title: str | None, filename: str | None, size) -> tuple[SessionResource, str]:
    from . import program
    if storage() is None:
        raise MediaError("Video storage isn't set up yet.")
    if week_key not in {w.key for w in program.load().weeks}:
        raise MediaError("Choose a session.")
    name = _safe_name(filename or "")
    ext = os.path.splitext(name)[1]
    allowed, limit = (VIDEO_TYPES, VIDEO_MAX) if kind == "video" else (FILE_TYPES, FILE_MAX) if kind == "file" else ({}, 0)
    if ext not in allowed:
        raise MediaError("Videos need to be .mp4 files." if kind == "video"
                         else "Handouts can be PDF, Word, PowerPoint, Excel, PNG or JPG.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    if not 0 < size <= limit:
        raise MediaError("That file is too large." if size > limit else "That file looks empty.")
    title = " ".join((title or "").split())[:TITLE_MAX] or os.path.splitext(name)[0]
    rid = uuid.uuid4()
    res = SessionResource(id=rid, week_key=week_key, kind=kind, title=title, filename=name,
                          content_type=allowed[ext], size=size, s3_key=f"media/{week_key}/{rid}/{name}",
                          position=_next_position(week_key))
    db.session.add(res)
    db.session.commit()
    return res, storage().upload_url(res.s3_key, res.content_type)


def finish_upload(res: SessionResource) -> bool:
    size = storage().size(res.s3_key)
    if not size:
        return False
    res.size, res.ready = size, True
    db.session.commit()
    return True


def add_link(week_key: str, title: str | None, url: str | None) -> SessionResource:
    from . import program
    if week_key not in {w.key for w in program.load().weeks}:
        raise MediaError("Choose a session.")
    url = (url or "").strip()
    if not re.match(r"^https://[^\s]+$", url) or len(url) > 500:
        raise MediaError("Links need to start with https://")
    title = " ".join((title or "").split())[:TITLE_MAX]
    if not title:
        raise MediaError("Give the link a title.")
    res = SessionResource(week_key=week_key, kind="link", title=title, url=url, ready=True,
                          position=_next_position(week_key))
    db.session.add(res)
    db.session.commit()
    return res


def _next_position(week_key: str) -> int:
    return 1 + max([r.position for r in for_week(week_key, include_pending=True)] or [0])


def get(resource_id) -> SessionResource:
    try:
        res = db.session.get(SessionResource, uuid.UUID(str(resource_id)))
    except ValueError:
        res = None
    if res is None:
        raise KeyError(resource_id)
    return res


def delete(res: SessionResource) -> None:
    if res.s3_key and storage() is not None:
        storage().delete(res.s3_key)
    db.session.delete(res)
    db.session.commit()


def for_week(week_key: str, include_pending: bool = False) -> list[SessionResource]:
    q = select(SessionResource).where(SessionResource.week_key == week_key)
    if not include_pending:
        q = q.where(SessionResource.ready.is_(True))
    return list(db.session.scalars(q.order_by(SessionResource.position, SessionResource.created_at)))


def weeks_with_video() -> set[str]:
    return set(db.session.scalars(select(SessionResource.week_key).where(
        SessionResource.kind == "video", SessionResource.ready.is_(True))))


def play_url(res: SessionResource) -> str:
    return storage().view_url(res.s3_key, res.content_type, PLAY_FOR)


def download_url(res: SessionResource) -> str:
    return storage().view_url(res.s3_key, res.content_type, DOWNLOAD_FOR, download_as=res.filename)


def nice_size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""
