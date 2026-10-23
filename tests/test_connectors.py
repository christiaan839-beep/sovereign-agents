import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from sovereign_agents.connectors.business import Business, load_sample_business
from sovereign_agents.connectors.mailbox import OutgoingMail
from sovereign_agents.connectors.outbox import Outbox
from sovereign_agents.connectors.suppliers import OrderLine, PurchaseOrder
from tests.helpers import SAMPLE, SAMPLES, sample_business


def test_the_sample_business_is_labelled_and_made_up(tmp_path: Path) -> None:
    business = sample_business(tmp_path)
    assert (business.slug, business.name, business.currency, business.sample) == (
        "sample-bakery",
        "Sample Bakery",
        "ZAR",
        True,
    )
    about = json.loads((SAMPLES / SAMPLE / "business.json").read_text("utf-8"))
    assert "made-up" in about["about"]
    addresses = [m.sender for m in business.mailbox.list_messages()]
    addresses += [s.email for s in business.suppliers.list_suppliers()]
    assert all(address.endswith(".example") for address in addresses)


def test_the_mailbox_reads_messages(tmp_path: Path) -> None:
    mailbox = sample_business(tmp_path).mailbox
    messages = mailbox.list_messages()
    assert [m.message_id for m in messages] == ["msg-001", "msg-002", "msg-003", "msg-004"]
    first = mailbox.get_message("msg-001")
    assert first is not None
    assert first.sender == "orders@flour-mill.example"
    assert mailbox.get_message("msg-999") is None


def test_the_sample_stock_and_suppliers_are_read(tmp_path: Path) -> None:
    business = sample_business(tmp_path)
    levels = {level.sku: level for level in business.stock.levels()}
    assert levels["FLR-WHITE-25"].on_hand == 3
    assert levels["FLR-WHITE-25"].reorder_point == 6
    suppliers = {s.supplier_id: s for s in business.suppliers.list_suppliers()}
    assert suppliers["sup-flour"].skus == ("FLR-WHITE-25", "FLR-WHOLE-25")


def test_placing_an_order_writes_the_outbox_once(tmp_path: Path) -> None:
    suppliers = sample_business(tmp_path).suppliers
    order = PurchaseOrder(
        idempotency_key="f" * 64,
        supplier_id="sup-flour",
        lines=(OrderLine("FLR-WHITE-25", 6, 38_500),),
        total_cents=231_000,
        currency="ZAR",
    )
    assert suppliers.place_order(order) == "po-ffffffffffff"
    assert suppliers.place_order(order) == "po-ffffffffffff"
    records = Outbox(tmp_path / "orders.jsonl").records()
    assert records == [
        {
            "currency": "ZAR",
            "idempotencyKey": "f" * 64,
            "lines": [{"quantity": 6, "sku": "FLR-WHITE-25", "unitPriceCents": 38_500}],
            "reference": "po-ffffffffffff",
            "supplierId": "sup-flour",
            "totalCents": 231_000,
        }
    ]


def test_sending_mail_writes_the_outbox_once(tmp_path: Path) -> None:
    mailbox = sample_business(tmp_path).mailbox
    mail = OutgoingMail(
        idempotency_key="e" * 64,
        to="orders@flour-mill.example",
        subject="Order po-eeeeeeeeeeee",
        body="Six bags, please.",
        in_reply_to="msg-001",
    )
    assert mailbox.send(mail) == "mail-eeeeeeeeeeee"
    mailbox.send(mail)
    assert [r["to"] for r in Outbox(tmp_path / "mail.jsonl").records()] == [
        "orders@flour-mill.example"
    ]


def test_an_empty_outbox_has_no_records(tmp_path: Path) -> None:
    assert Outbox(tmp_path / "none.jsonl").records() == []


def copy_sample(tmp_path: Path) -> Path:
    root = tmp_path / "samples"
    shutil.copytree(SAMPLES / SAMPLE, root / SAMPLE)
    return root


def rewrite(path: Path, edit: dict[str, object]) -> None:
    document = json.loads(path.read_text("utf-8"))
    document.update(edit)
    path.write_text(json.dumps(document), "utf-8")


def test_a_business_not_labelled_as_a_sample_is_refused(tmp_path: Path) -> None:
    root = copy_sample(tmp_path)
    rewrite(root / SAMPLE / "business.json", {"sample": False})
    with pytest.raises(ValueError, match='"sample": true'):
        load_sample_business(root, SAMPLE, tmp_path / "out")


def test_a_slug_that_does_not_match_its_folder_is_refused(tmp_path: Path) -> None:
    root = copy_sample(tmp_path)
    rewrite(root / SAMPLE / "business.json", {"slug": "other"})
    with pytest.raises(ValueError, match="slug does not match"):
        load_sample_business(root, SAMPLE, tmp_path / "out")


READERS: dict[str, Callable[[Business], object]] = {
    "stock.json": lambda business: business.stock.levels(),
    "inbox.json": lambda business: business.mailbox.list_messages(),
    "suppliers.json": lambda business: business.suppliers.list_suppliers(),
}
BAD_ITEM = {"sku": "A", "name": "a", "reorder_point": 1}
BAD_MESSAGE = {"message_id": "", "from": "a", "subject": "s", "received_at": "t", "body": "b"}
BAD_SUPPLIER = {"supplier_id": "s", "name": "n", "email": "e", "skus": "A"}


@pytest.mark.parametrize(
    ("file", "edit", "message"),
    [
        ("stock.json", {"items": [{**BAD_ITEM, "on_hand": 1.5}]}, "whole number"),
        ("stock.json", {"items": [{**BAD_ITEM, "on_hand": True}]}, "whole number"),
        ("stock.json", {"items": "none"}, "items list"),
        ("stock.json", {"items": ["A"]}, "must be an object"),
        ("inbox.json", {"messages": [BAD_MESSAGE]}, "non-empty string"),
        ("suppliers.json", {"suppliers": [BAD_SUPPLIER]}, "list of strings"),
    ],
)
def test_sample_files_are_read_strictly(
    tmp_path: Path, file: str, edit: dict[str, object], message: str
) -> None:
    root = copy_sample(tmp_path)
    rewrite(root / SAMPLE / file, edit)
    business = load_sample_business(root, SAMPLE, tmp_path / "out")
    with pytest.raises(ValueError, match=message):
        READERS[file](business)
