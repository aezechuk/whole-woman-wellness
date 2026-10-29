"""Optional journal PIN: an extra lock on reflections and assessment results.

Why: sign-in lasts 14 days on her phone, so anyone holding the unlocked phone could
open the app. With a PIN, the private pages (journal weeks, results, data download)
need the PIN again after 15 minutes without activity, or when she taps Lock now.

- Only a slow, salted hash of the PIN (keyed with the server secret) is stored.
- Unlock state lives on the server-side sign-in session, so it is per device and a
  fresh sign-in always starts locked.
- 5 wrong PINs locks it until she resets it with a code sent to her email.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta
from functools import wraps

from flask import current_app, g, redirect, request, url_for, Response
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .models import Participant, utcnow

UNLOCK_FOR = timedelta(minutes=15)
MAX_TRIES = 5
_TOUCH_EVERY = timedelta(minutes=1)
_PIN = re.compile(r"^\d{4,6}$")


class PinError(ValueError):
    pass


def _peppered(p: Participant, pin: str) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"pin:{p.id}:{pin}".encode(), hashlib.sha256).hexdigest()


def _aware(dt):
    from datetime import timezone
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validate(pin: str | None, confirm: str | None) -> str:
    pin = (pin or "").strip()
    if not _PIN.match(pin):
        raise PinError("Your PIN needs to be 4 to 6 numbers.")
    if pin != (confirm or "").strip():
        raise PinError("Those two PINs didn't match. Please try again.")
    return pin


def set_pin(p: Participant, pin: str) -> None:
    p.pin_hash = generate_password_hash(_peppered(p, pin))
    p.pin_failures = 0
    unlock()                       # she just chose it on this device
    db.session.commit()


def remove_pin(p: Participant) -> None:
    p.pin_hash, p.pin_failures = None, 0
    db.session.commit()


def locked_out(p: Participant) -> bool:
    return bool(p.pin_hash) and (p.pin_failures or 0) >= MAX_TRIES


def tries_left(p: Participant) -> int:
    return max(0, MAX_TRIES - (p.pin_failures or 0))


def check(p: Participant, pin: str | None) -> bool:
    """True and unlocks this device if the PIN is right; counts wrong tries."""
    if not p.pin_hash or locked_out(p):
        return False
    if check_password_hash(p.pin_hash, _peppered(p, (pin or "").strip())):
        p.pin_failures = 0
        unlock()
        db.session.commit()
        return True
    p.pin_failures = (p.pin_failures or 0) + 1
    db.session.commit()
    return False


def _session():
    return g.get("auth_session")


def unlocked() -> bool:
    p = g.get("participant")
    if p is None or not p.pin_hash:
        return True
    sess = _session()
    return bool(sess and sess.pin_unlocked_until and _aware(sess.pin_unlocked_until) > utcnow())


def unlock() -> None:
    sess = _session()
    if sess is not None:
        sess.pin_unlocked_until = utcnow() + UNLOCK_FOR


def lock() -> None:
    sess = _session()
    if sess is not None and sess.pin_unlocked_until is not None:
        sess.pin_unlocked_until = None
        db.session.commit()


def touch() -> None:
    """Any activity while unlocked keeps it unlocked for another 15 minutes."""
    p, sess = g.get("participant"), _session()
    if p is None or not p.pin_hash or sess is None or not unlocked():
        return
    if UNLOCK_FOR - (_aware(sess.pin_unlocked_until) - utcnow()) >= _TOUCH_EVERY:
        unlock()
        db.session.commit()


def safe_next(target: str | None, default: str) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def pin_required(view):
    """For pages showing reflections or results. Use under @login_required."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        p = g.participant
        if p is not None and p.pin_hash:
            g.pin_private = True              # base.html: Lock now bar + app-switcher veil
            if not unlocked():
                if request.method != "GET":
                    if request.headers.get("X-Autosave") == "1":
                        return Response(status=423)
                    return redirect(url_for("reflect.pin_unlock"))
                nxt = request.full_path.rstrip("?")
                if request.endpoint == "reflect.download":
                    nxt = url_for("reflect.settings")
                return redirect(url_for("reflect.pin_unlock", next=nxt))
        return view(*args, **kwargs)
    return wrapped
