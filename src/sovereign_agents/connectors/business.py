"""A business: who it is and the connectors its jobs use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sovereign_agents.kit import check_slug

from .files import read_json, text
from .mailbox import Mailbox, SampleMailbox
from .stock import SampleStock, Stock
from .suppliers import SampleSuppliers, Suppliers


@dataclass(frozen=True)
class Business:
    slug: str
    name: str
    currency: str
    sample: bool
    mailbox: Mailbox
    stock: Stock
    suppliers: Suppliers


def load_sample_business(samples_root: Path, slug: str, outbox_dir: Path) -> Business:
    """A made-up business from ``samples/<slug>/``; its outbox goes to ``outbox_dir``."""
    folder = samples_root / check_slug(slug)
    about = read_json(folder / "business.json")
    where = str(folder / "business.json")
    if not isinstance(about, dict) or about.get("sample") is not True:
        raise ValueError(f'{where}: a sample business must say "sample": true')
    if text(about, "slug", where) != slug:
        raise ValueError(f"{where}: slug does not match the folder")
    return Business(
        slug=slug,
        name=text(about, "name", where),
        currency=text(about, "currency", where),
        sample=True,
        mailbox=SampleMailbox(folder / "inbox.json", outbox_dir),
        stock=SampleStock(folder / "stock.json"),
        suppliers=SampleSuppliers(folder / "suppliers.json", outbox_dir),
    )
