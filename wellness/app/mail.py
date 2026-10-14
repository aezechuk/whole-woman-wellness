"""Outbound email. Backends: memory (tests), console (dev), ses (production)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flask import current_app, render_template

log = logging.getLogger(__name__)

# Tests read sent mail from here.
OUTBOX: list[Message] = []


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str


def send(to: str, subject: str, template: str, **context) -> None:
    msg = Message(
        to=to,
        subject=subject,
        text=render_template(f"email/{template}.txt", **context),
        html=render_template(f"email/{template}.html", **context),
    )
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        OUTBOX.append(msg)
    elif backend == "console":
        # Never log addresses or bodies, only that something was sent.
        log.info("mail: template=%s (console backend, not delivered)", template)
        if current_app.debug:
            print(f"\n--- DEV EMAIL to {to}: {subject}\n{msg.text}\n---\n", flush=True)
    elif backend == "ses":
        _send_ses(msg)
    else:
        raise RuntimeError(f"unknown MAIL_BACKEND {backend!r}")


def _send_ses(msg: Message) -> None:
    import boto3  # imported lazily; credentials come from the EC2 instance role

    client = boto3.client("sesv2")
    client.send_email(
        FromEmailAddress=current_app.config["MAIL_FROM"],
        Destination={"ToAddresses": [msg.to]},
        Content={"Simple": {
            "Subject": {"Data": msg.subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": msg.text, "Charset": "UTF-8"},
                "Html": {"Data": msg.html, "Charset": "UTF-8"},
            },
        }},
    )
    log.info("mail: sent via SES")
