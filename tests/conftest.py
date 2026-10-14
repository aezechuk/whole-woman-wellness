import re

import pytest

from wellness.app import create_app, mail
from wellness.app.auth.service import hash_join_code
from wellness.app.extensions import db
from wellness.app.models import Cohort

JOIN_CODE = "WIARTS26"


@pytest.fixture
def app():
    app = create_app("test")
    with app.app_context():
        db.create_all()
        db.session.add(Cohort(name="Fall 2026", join_code_hash=hash_join_code(JOIN_CODE)))
        db.session.commit()
        mail.OUTBOX.clear()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def last_code() -> str:
    return re.search(r"\b(\d{6})\b", mail.OUTBOX[-1].text).group(1)


def sign_in(client, email="maya@example.com", join_code=JOIN_CODE):
    client.post("/start", data={"email": email, "join_code": join_code})
    return client.post("/verify", data={"code": last_code()})


def onboard(client, email="maya@example.com", name="Maya", mentee="yes"):
    sign_in(client, email)
    client.post("/welcome", data={"first_name": name, "mentee": mentee})
    client.post("/consent", data={"agree": "yes"})
