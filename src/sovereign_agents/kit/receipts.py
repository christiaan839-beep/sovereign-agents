# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Each business's own Ed25519 key, and the receipts signed with it.

Receipts are VAOS v2 receipts from verifiable-receipts: ``canonical`` is the exact signed
text, ``contentHash`` its SHA-256 and ``signature`` an Ed25519 signature over it. Anyone with
the business's public key can check one with ``verify`` here, or with the Go and JavaScript
verifiers in verifiable-receipts.

What it guarantees: every receipt the core issues carries a header the body cannot overwrite
(``v``, ``type``, ``id``, ``business``, ``createdAt``) and is stored in the ledger as signed.
A body holds only strings, integers, booleans, null, lists and objects, so the signed text is
the same in Python, Go and JavaScript.

What it refuses: a float anywhere in a receipt; writing a private key inside a git work tree;
overwriting a private key; loading a private key that others can read, that is not Ed25519,
or whose public key file does not match; loading a public key the receipts verifiers refuse
(VAOS 2.0 section 7.3: a point of small or mixed order, off the curve, or a non-canonical
encoding), since every receipt under such a key is rejected. The private key is written once
with mode 0600 and is never printed, logged or put in a receipt.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sovereign_matrix.verifiable_receipts import (
    VerifyResult,
    ed25519_public_key_problem,
    sign_v2_receipt,
    verify_v2_receipt,
)

from .clock import iso
from .ledger import Ledger, ReceiptKind, new_id
from .names import check_slug

PRIVATE_KEY_FILE = "ed25519-private.pem"
# The receipts spec publishes the public key as /.well-known/sovereign-receipts/ed25519.pem.
PUBLIC_KEY_FILE = "ed25519.pem"


class KeyFileError(Exception):
    """A key file is missing, exposed, of the wrong kind, or would be overwritten."""


def inside_git_worktree(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_key_problem(public_key_pem: bytes) -> str | None:
    """Why the receipts verifiers refuse this PEM public key, or None when they accept it.

    The reasons are the verifiers' own (VAOS 2.0 section 7.3), such as "public key is not in
    the prime-order subgroup" for a key of mixed order. A key made by ``new_keys`` is always
    accepted. Any reason the verifiers give refuses the key.
    """
    try:
        key = serialization.load_pem_public_key(public_key_pem)
    except UnsupportedAlgorithm:
        # A key on a curve cryptography does not support, such as secp112r1.
        return "not an Ed25519 public key"
    except ValueError:
        return "not a PEM public key"
    if not isinstance(key, Ed25519PublicKey):
        return "not an Ed25519 public key"
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return ed25519_public_key_problem(raw)


def _check_public_key(path: Path, public_key_pem: bytes) -> None:
    problem = public_key_problem(public_key_pem)
    if problem is not None:
        raise KeyFileError(
            f"{path} is refused: {problem}; every receipt under it would be rejected, "
            "so make a new key pair with new_keys()"
        )


def new_keys(keys_dir: Path) -> Path:
    """Make a new key pair in ``keys_dir`` and return the public key's path.

    Refuses a directory inside a git work tree and never overwrites a private key.
    """
    keys_dir = keys_dir.expanduser().resolve()
    if inside_git_worktree(keys_dir):
        raise KeyFileError(f"{keys_dir} is inside a git work tree; keys live outside the repo")
    private_path = keys_dir / PRIVATE_KEY_FILE
    if private_path.exists():
        raise KeyFileError(f"{private_path} exists; make new keys elsewhere, never overwrite")
    keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    os.chmod(private_path, 0o600)
    public_path = keys_dir / PUBLIC_KEY_FILE
    public_path.write_bytes(public_pem(key))
    return public_path


@dataclass(frozen=True)
class Signer:
    business: str
    private_key: Ed25519PrivateKey = field(repr=False)  # gitleaks:allow (a type, not a key)
    public_pem: bytes

    def sign(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return sign_v2_receipt(body, self.private_key)


def load_signer(business: str, keys_dir: Path) -> Signer:
    """The signer for ``business`` from ``keys_dir``, after the checks above."""
    check_slug(business)
    private_path = keys_dir / PRIVATE_KEY_FILE
    if not private_path.is_file():
        raise KeyFileError(f"no key at {private_path}; make one with new_keys()")
    mode = private_path.stat().st_mode & 0o777
    if mode & 0o077:
        raise KeyFileError(f"{private_path} has mode {mode:o}; others can read it (chmod 600)")
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyFileError(f"{private_path} is not an Ed25519 private key")
    public = public_pem(key)
    public_path = keys_dir / PUBLIC_KEY_FILE
    if public_path.exists() and public_path.read_bytes() != public:
        raise KeyFileError(f"{public_path} does not match the private key")
    # The public key of an Ed25519 private key is always of order L; checked all the same.
    _check_public_key(private_path, public)
    return Signer(business, key, public)


def load_public_pem(keys_dir: Path) -> bytes:
    public_path = keys_dir / PUBLIC_KEY_FILE
    if not public_path.is_file():
        raise KeyFileError(f"no public key at {public_path}")
    public = public_path.read_bytes()
    _check_public_key(public_path, public)
    return public


def sign_receipt(
    *, signer: Signer, kind: ReceiptKind, body: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Sign ``body`` under the kit's receipt header, without storing it; its ``id`` is new."""
    header = {
        "v": 1,
        "type": f"sovereign-agents/{kind}",
        "id": new_id("rcp"),
        "business": signer.business,
        "createdAt": iso(now),
    }
    return signer.sign({**body, **header})


def issue_receipt(
    *,
    ledger: Ledger,
    signer: Signer,
    kind: ReceiptKind,
    subject_id: str,
    body: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Sign ``body`` under the kit's receipt header and store the receipt in the ledger."""
    receipt = sign_receipt(signer=signer, kind=kind, body=body, now=now)
    ledger.add_receipt(
        receipt_id=str(receipt["id"]),
        business=signer.business,
        kind=kind,
        subject_id=subject_id,
        receipt=receipt,
        now=now,
    )
    return receipt


def verify(receipt: Mapping[str, Any], public_key_pem: bytes) -> VerifyResult:
    return verify_v2_receipt(receipt, public_key_pem)


def verify_all(
    ledger: Ledger, business: str, public_key_pem: bytes
) -> list[tuple[str, VerifyResult]]:
    """Every receipt the ledger holds for ``business``, each with its check."""
    return [
        (str(receipt.get("id", "?")), verify(receipt, public_key_pem))
        for receipt in ledger.receipts(business)
    ]
