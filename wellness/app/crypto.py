"""Envelope encryption for private reflections.

Each entry gets its own 256-bit data key. The entry is sealed with AES-GCM using
that key; the data key itself is wrapped by a key-encryption key:

- "kms" (production): AWS KMS. The KMS key policy lets only the app's instance
  role decrypt, and every decrypt is logged in CloudTrail. No break-glass path.
- "local" (development/tests): a key from the environment.

The participant id and the field name are bound into both layers (KMS
EncryptionContext and AES-GCM associated data), so a ciphertext copied onto
another participant's row will not decrypt.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app

VERSION = 1


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _aad(context: dict[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":")).encode()


class _LocalKeys:
    def __init__(self, key_b64: str):
        key = _unb64(key_b64)
        if len(key) != 32:
            raise RuntimeError("JOURNAL_LOCAL_KEY must be 32 bytes, base64-encoded")
        self._kek = AESGCM(key)

    def generate(self, context):
        dk = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        return dk, nonce + self._kek.encrypt(nonce, dk, _aad(context))

    def unwrap(self, wrapped: bytes, context) -> bytes:
        return self._kek.decrypt(wrapped[:12], wrapped[12:], _aad(context))


class _KmsKeys:
    def __init__(self, key_id: str):
        import boto3
        self._kms = boto3.client("kms")
        self._key_id = key_id

    def generate(self, context):
        r = self._kms.generate_data_key(KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=context)
        return r["Plaintext"], r["CiphertextBlob"]

    def unwrap(self, wrapped: bytes, context) -> bytes:
        return self._kms.decrypt(CiphertextBlob=wrapped, EncryptionContext=context,
                                 KeyId=self._key_id)["Plaintext"]


def _keys():
    cfg = current_app.config
    cache = current_app.extensions.setdefault("journal_keys", {})
    if "k" not in cache:
        if cfg["JOURNAL_KEY_BACKEND"] == "kms":
            cache["k"] = _KmsKeys(cfg["JOURNAL_KMS_KEY_ID"])
        else:
            cache["k"] = _LocalKeys(cfg["JOURNAL_LOCAL_KEY"])
    return cache["k"]


def seal(plaintext: str, context: dict[str, str]) -> str:
    dk, wrapped = _keys().generate(context)
    nonce = os.urandom(12)
    ct = AESGCM(dk).encrypt(nonce, plaintext.encode("utf-8"), _aad(context))
    return json.dumps({"v": VERSION, "k": _b64(wrapped), "n": _b64(nonce), "c": _b64(ct)})


def open_(sealed: str, context: dict[str, str]) -> str:
    blob = json.loads(sealed)
    if blob.get("v") != VERSION:
        raise ValueError("unknown ciphertext version")
    dk = _keys().unwrap(_unb64(blob["k"]), context)
    return AESGCM(dk).decrypt(_unb64(blob["n"]), _unb64(blob["c"]), _aad(context)).decode("utf-8")
