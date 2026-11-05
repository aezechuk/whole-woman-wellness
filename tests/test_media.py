from sqlalchemy import func, select

from wellness.app import media
from wellness.app.extensions import db
from wellness.app.models import SessionResource

from .conftest import onboard
from .test_admin import admin_sign_in


def upload(admin, week="reset", kind="video", name="replay.mp4", size=900_000_000, title="Session replay"):
    r = admin.post("/admin/resources/upload-url",
                   data={"week_key": week, "kind": kind, "filename": name, "size": str(size), "title": title})
    return r


def finish(app, admin, rid, size=900_000_000):
    res = media.get(rid)
    media.storage().objects[res.s3_key] = size        # the browser PUT to S3
    return admin.post(f"/admin/resources/{rid}/done")


def test_upload_flow_and_participant_sees_video_without_download(app):
    admin = app.test_client()
    admin_sign_in(admin)
    r = upload(admin)
    assert r.status_code == 200 and "upload=1" in r.json["url"] and r.json["content_type"] == "video/mp4"
    p = app.test_client()
    onboard(p)
    assert "SESSION REPLAY" not in p.get("/sessions/reset").data.decode()     # not until it arrives
    assert finish(app, admin, r.json["id"]).status_code == 200
    page = p.get("/sessions/reset").data.decode()
    assert "SESSION REPLAY" in page and 'controlslist="nodownload"' in page and "expires=14400" in page
    assert "▶ Replay" in p.get("/sessions").data.decode()
    assert p.get(f"/resources/{r.json['id']}/download").status_code == 404   # videos are watch-only


def test_handout_downloads_for_signed_in_only(app):
    admin = app.test_client()
    admin_sign_in(admin)
    r = upload(admin, kind="file", name="Breathing Practice.pdf", size=200_000, title="Breathing practice")
    finish(app, admin, r.json["id"], 200_000)
    anon = app.test_client()
    assert anon.get(f"/resources/{r.json['id']}/download").status_code == 302
    assert "/start" in anon.get(f"/resources/{r.json['id']}/download").headers["Location"]
    p = app.test_client()
    onboard(p)
    assert "Breathing practice" in p.get("/sessions/reset").data.decode()
    loc = p.get(f"/resources/{r.json['id']}/download").headers["Location"]
    assert "download=1" in loc and "expires=300" in loc


def test_validation(app):
    admin = app.test_client()
    admin_sign_in(admin)
    assert upload(admin, name="replay.mov").status_code == 400
    assert upload(admin, kind="file", name="script.exe").status_code == 400
    assert upload(admin, size=6 * 1024 ** 3).status_code == 400
    assert upload(admin, week="nope").status_code == 400
    assert admin.post(f"/admin/resources/{upload(admin).json['id']}/done").status_code == 409   # never arrived
    assert "upload didn't finish" in admin.get("/admin/resources").data.decode()
    admin.post("/admin/resources/link", data={"week_key": "reset", "title": "x", "url": "javascript:alert(1)"})
    assert db.session.scalar(select(func.count()).select_from(SessionResource).where(SessionResource.kind == "link")) == 0


def test_participants_cannot_upload_and_admin_can_remove(app):
    p = app.test_client()
    onboard(p)
    assert upload(p).status_code == 302                                     # bounced to admin sign-in
    admin = app.test_client()
    admin_sign_in(admin)
    rid = upload(admin).json["id"]
    finish(app, admin, rid)
    key = media.get(rid).s3_key
    admin.post(f"/admin/resources/{rid}/delete")
    assert key not in media.storage().objects
    assert "SESSION REPLAY" not in p.get("/sessions/reset").data.decode()


def test_link_and_csp(app):
    admin = app.test_client()
    admin_sign_in(admin)
    admin.post("/admin/resources/link", data={"week_key": "reset", "title": "Playlist", "url": "https://example.com/x"})
    p = app.test_client()
    onboard(p)
    r = p.get("/sessions/reset")
    assert "Playlist" in r.data.decode() and "RESOURCES" in r.data.decode()
    assert "media-src 'self' https://media.test" in r.headers["Content-Security-Policy"]
