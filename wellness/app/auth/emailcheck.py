"""Catch incomplete or mistyped email addresses before a code is sent to nowhere.

Format check: one @, a plausible local part, and a domain with a real-looking
ending (letters, at least two). Typo check: common misspellings of the big
providers and of ".com". Suggestions are only offered, never applied silently.
"""

from __future__ import annotations

import re

_LOCAL = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

_DOMAIN_TYPOS = {
    "gmail.com": ["gmial.com", "gmai.com", "gmal.com", "gamil.com", "gnail.com", "gmaill.com", "gmail.co",
                  "gmail.cm", "gmil.com", "gmaik.com", "gmsil.com", "gmail.om", "gmail.comm", "gmali.com"],
    "yahoo.com": ["yaho.com", "yahooo.com", "yhoo.com", "yahoo.co", "yahho.com", "yaoo.com", "yahoo.cm"],
    "hotmail.com": ["hotmial.com", "hotmai.com", "hotmal.com", "hotnail.com", "hotmail.co", "hotamil.com",
                    "hotmaill.com", "hotmail.cm"],
    "outlook.com": ["outlok.com", "outloo.com", "outlook.co", "outlool.com", "outllok.com", "otlook.com"],
    "icloud.com": ["iclod.com", "icloud.co", "icoud.com", "iclould.com", "icluod.com", "icloud.cm"],
    "aol.com": ["aol.co", "aoll.com", "aol.cm"],
    "comcast.net": ["comcast.com", "comcst.net"],
}
_TYPO_TO_DOMAIN = {typo: real for real, typos in _DOMAIN_TYPOS.items() for typo in typos}
_TLD_TYPOS = {"con": "com", "cmo": "com", "ocm": "com", "vom": "com", "xom": "com", "comm": "com", "cpm": "com",
              "nte": "net", "ner": "net", "ogr": "org", "prg": "org"}


def is_valid(email: str) -> bool:
    if not email or len(email) > 254 or email.count("@") != 1 or any(c.isspace() for c in email):
        return False
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or not _LOCAL.match(local):
        return False
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return False
    tld = labels[-1]
    return tld.isalpha() and len(tld) >= 2


def suggestion(email: str) -> str | None:
    """A likely intended address for a common typo, or None."""
    if "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    domain = domain.lower()
    if domain in _TYPO_TO_DOMAIN:
        return f"{local}@{_TYPO_TO_DOMAIN[domain]}"
    head, _, tld = domain.rpartition(".")
    if head and tld in _TLD_TYPOS:
        fixed = f"{head}.{_TLD_TYPOS[tld]}"
        return f"{local}@{_TYPO_TO_DOMAIN.get(fixed, fixed)}"
    return None
