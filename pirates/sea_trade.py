"""Finite, contact-local at-sea trading against an explicitly returned offer."""
from __future__ import annotations

from copy import deepcopy
from hashlib import blake2b
import json


TRADE_ITEMS = ("beans", "grain", "iron")
MAX_OFFER_QUANTITY = 999
MAX_TRANSACTION_QUANTITY = 20
MAX_TRANSACTIONS_PER_CONTACT = 50
MAX_TRANSACTION_SEQUENCE = 1_000_000


def _stable_id(payload):
    return blake2b(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8"),
        digest_size=12).hexdigest()


def offer_id(contact_instance_id, issued_tick):
    return _stable_id(["sea-offer-v1", contact_instance_id, issued_tick])


def transaction_id(contact_instance_id, sequence, offer, item_id, quantity,
                   buying, unit_price):
    return _stable_id(["sea-trade-v1", contact_instance_id, sequence, offer,
                       item_id, quantity, buying, unit_price])


def _prices(item_id):
    from pirates.economy import CATALOG

    if item_id not in TRADE_ITEMS or item_id not in CATALOG:
        raise ValueError("That item is not offered for at-sea trade")
    base = CATALOG[item_id]["base_price"]
    # The counterpart states a fixed ask and bid. No remote port market or
    # hidden scarcity quote is consulted; buying and selling cannot guarantee
    # an arbitrage loop.
    return max(1, (base * 125) // 100), max(1, (base * 70) // 100)


def create_offer(contact_instance_id, ship, tick):
    """Return only the finite commodity subset explicitly offered by a trader."""
    if (not isinstance(contact_instance_id, str) or not contact_instance_id
            or type(tick) is not int or tick < 0
            or not isinstance(ship, dict) or ship.get("threat") != "merchant"):
        raise ValueError("A finite at-sea offer requires a valid merchant contact")
    cargo = ship.get("cargo")
    silver = ship.get("silver")
    if (not isinstance(cargo, dict) or type(silver) is not int or silver < 0):
        raise ValueError("Merchant trade ledgers are unavailable")
    items = []
    for item_id in TRADE_ITEMS:
        stock = cargo.get(item_id, 0)
        if type(stock) is not int or stock < 0:
            raise ValueError("Merchant trade stock is invalid")
        if stock == 0 and silver == 0:
            continue
        ask, bid = _prices(item_id)
        sell_quantity = min(MAX_OFFER_QUANTITY, stock)
        buy_quantity = min(MAX_OFFER_QUANTITY, silver // bid)
        if sell_quantity or buy_quantity:
            items.append({"item_id": item_id,
                          "sell_quantity": sell_quantity,
                          "sell_price": ask,
                          "buy_quantity": buy_quantity,
                          "buy_price": bid})
    if not items:
        raise ValueError("This merchant has no finite commodity offer to disclose")
    return {"offer_id": offer_id(contact_instance_id, tick),
            "issued_tick": tick, "items": items}


def quote(contact_instance_id, offer, pending, receipts, sequence, item_id,
          quantity, buying):
    if offer is None:
        raise ValueError("Request the merchant's explicit finite offer first")
    if pending is not None:
        raise ValueError("A sea-trade quote is already awaiting confirmation")
    if len(receipts) >= MAX_TRANSACTIONS_PER_CONTACT:
        raise ValueError("This contact's bounded trade allowance is exhausted")
    if (type(quantity) is not int or not 1 <= quantity <= MAX_TRANSACTION_QUANTITY
            or type(buying) is not bool):
        raise ValueError("Choose a trade quantity from 1 to 20")
    row = next((row for row in offer["items"] if row["item_id"] == item_id), None)
    if row is None:
        raise ValueError("That commodity is not on this merchant's returned offer")
    available = row["sell_quantity"] if buying else row["buy_quantity"]
    unit_price = row["sell_price"] if buying else row["buy_price"]
    if quantity > available:
        raise ValueError("That quantity exceeds the merchant's finite offered amount")
    if (type(sequence) is not int or not 0 <= sequence < MAX_TRANSACTION_SEQUENCE):
        raise ValueError("This contact's trade sequence is exhausted")
    next_sequence = sequence + 1
    return {"transaction_id": transaction_id(
                contact_instance_id, next_sequence, offer["offer_id"], item_id,
                quantity, buying, unit_price),
            "sequence": next_sequence, "offer_id": offer["offer_id"],
            "item_id": item_id, "quantity": quantity, "buying": buying,
            "unit_price": unit_price, "total": unit_price * quantity,
            "quoted_tick": None}


def validate_contact_trade(contact, contact_instance_id, current_tick):
    """Strictly validate persisted offer, quote, and idempotency receipts."""
    if (type(contact.get("trade_sequence")) is not int
            or not 0 <= contact["trade_sequence"] < MAX_TRANSACTION_SEQUENCE):
        raise ValueError("Invalid saved sea-trade sequence")
    offer = contact["trade_offer"]
    if offer is not None:
        if (not isinstance(offer, dict)
                or set(offer) != {"offer_id", "issued_tick", "items"}
                or type(offer["issued_tick"]) is not int
                or not 0 <= offer["issued_tick"] <= current_tick
                or offer["offer_id"] != offer_id(contact_instance_id, offer["issued_tick"])
                or not isinstance(offer["items"], list) or not offer["items"]
                or len(offer["items"]) > len(TRADE_ITEMS)):
            raise ValueError("Invalid saved sea-trade offer")
        seen = set()
        for row in offer["items"]:
            if (not isinstance(row, dict)
                    or set(row) != {"item_id", "sell_quantity", "sell_price",
                                    "buy_quantity", "buy_price"}
                    or row["item_id"] not in TRADE_ITEMS
                    or row["item_id"] in seen
                    or any(type(row[key]) is not int or row[key] < 0
                           for key in ("sell_quantity", "buy_quantity"))
                    or any(type(row[key]) is not int or row[key] < 1
                           for key in ("sell_price", "buy_price"))
                    or row["sell_quantity"] > MAX_OFFER_QUANTITY
                    or row["buy_quantity"] > MAX_OFFER_QUANTITY):
                raise ValueError("Invalid saved sea-trade offer")
            ask, bid = _prices(row["item_id"])
            if row["sell_price"] != ask or row["buy_price"] != bid:
                raise ValueError("Saved sea-trade offer price does not match its fixed terms")
            seen.add(row["item_id"])
    receipts = contact["trade_receipts"]
    if (not isinstance(receipts, list)
            or len(receipts) > MAX_TRANSACTIONS_PER_CONTACT):
        raise ValueError("Invalid saved sea-trade receipts")
    seen_transactions = set()
    seen_sequences = set()
    for row in receipts:
        required = {"transaction_id", "sequence", "offer_id", "item_id", "quantity",
                    "buying", "unit_price", "total", "quoted_tick", "committed_tick"}
        if (not isinstance(row, dict) or set(row) != required
                or offer is None or row["offer_id"] != offer["offer_id"]
                or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= contact["trade_sequence"]
                or not isinstance(row["offer_id"], str)
                or row["item_id"] not in TRADE_ITEMS
                or type(row["quantity"]) is not int
                or not 1 <= row["quantity"] <= MAX_TRANSACTION_QUANTITY
                or type(row["buying"]) is not bool
                or type(row["unit_price"]) is not int or row["unit_price"] < 1
                or type(row["total"]) is not int
                or row["total"] != row["quantity"] * row["unit_price"]
                or type(row["quoted_tick"]) is not int
                or type(row["committed_tick"]) is not int
                or not 0 <= row["quoted_tick"] <= row["committed_tick"] <= current_tick
                or row["transaction_id"] != transaction_id(
                    contact_instance_id, row["sequence"], row["offer_id"],
                    row["item_id"], row["quantity"], row["buying"], row["unit_price"])
                or row["transaction_id"] in seen_transactions):
            raise ValueError("Invalid saved sea-trade receipt")
        receipt_offer_row = next((item for item in offer["items"]
                                  if item["item_id"] == row["item_id"]), None)
        expected_receipt_price = None
        if receipt_offer_row is not None:
            expected_receipt_price = (receipt_offer_row["sell_price"] if row["buying"]
                                      else receipt_offer_row["buy_price"])
        if expected_receipt_price != row["unit_price"]:
            raise ValueError("Saved sea-trade receipt differs from its explicit offer")
        if row["sequence"] in seen_sequences:
            raise ValueError("Duplicate saved sea-trade sequence")
        seen_transactions.add(row["transaction_id"])
        seen_sequences.add(row["sequence"])
    pending = contact["trade_pending"]
    if pending is not None:
        required = {"transaction_id", "sequence", "offer_id", "item_id", "quantity",
                    "buying", "unit_price", "total", "quoted_tick"}
        if (not isinstance(pending, dict) or set(pending) != required
                or offer is None
                or type(pending["sequence"]) is not int
                or pending["sequence"] != contact["trade_sequence"]
                or pending["offer_id"] != offer["offer_id"]
                or pending["item_id"] not in TRADE_ITEMS
                or type(pending["quantity"]) is not int
                or not 1 <= pending["quantity"] <= MAX_TRANSACTION_QUANTITY
                or type(pending["buying"]) is not bool
                or type(pending["unit_price"]) is not int or pending["unit_price"] < 1
                or type(pending["total"]) is not int
                or pending["total"] != pending["quantity"] * pending["unit_price"]
                or type(pending["quoted_tick"]) is not int
                or not 0 <= pending["quoted_tick"] <= current_tick
                or pending["transaction_id"] != transaction_id(
                    contact_instance_id, pending["sequence"], pending["offer_id"],
                    pending["item_id"], pending["quantity"], pending["buying"],
                    pending["unit_price"])
                or pending["transaction_id"] in seen_transactions):
            raise ValueError("Invalid saved pending sea-trade quote")
        offer_row = next((row for row in offer["items"]
                          if row["item_id"] == pending["item_id"]), None)
        expected_price = (offer_row.get("sell_price") if offer_row and pending["buying"]
                          else offer_row.get("buy_price") if offer_row else None)
        expected_quantity = (offer_row.get("sell_quantity") if offer_row and pending["buying"]
                             else offer_row.get("buy_quantity") if offer_row else None)
        if (expected_price != pending["unit_price"]
                or expected_quantity is None or pending["quantity"] > expected_quantity):
            raise ValueError("Pending sea-trade quote is outside its explicit offer")


def offer_rows(offer):
    rows = ["This is a finite, explicit offer from the close-range merchant.",
            "No remote port prices or unlisted cargo are queried."]
    for item in offer["items"]:
        if item["sell_quantity"]:
            rows.append(f"Sell to you: {item['item_id']} — up to {item['sell_quantity']} "
                        f"at {item['sell_price']} Marks each")
        if item["buy_quantity"]:
            rows.append(f"Buy from you: {item['item_id']} — up to {item['buy_quantity']} "
                        f"at {item['buy_price']} Marks each")
    return rows


def quote_rows(quote_row):
    direction = "buy from merchant" if quote_row["buying"] else "sell to merchant"
    return [f"Transaction: {quote_row['transaction_id']}",
            f"Action: {direction} | {quote_row['quantity']} {quote_row['item_id']}",
            f"Fixed offer: {quote_row['unit_price']} Marks each; total {quote_row['total']} Marks",
            "Commit rechecks stock, purse, cargo capacity and reserved cargo before any mutation.",
            "Commit costs one minute; cancel costs nothing."]


def choices(offer):
    """Flatten the disclosed bid/ask into selectable, bounded transaction rows."""
    result = []
    for item in offer["items"]:
        if item["sell_quantity"]:
            result.append({"item_id": item["item_id"], "buying": True,
                           "max_quantity": min(item["sell_quantity"], MAX_TRANSACTION_QUANTITY),
                           "unit_price": item["sell_price"],
                           "label": (f"Buy {item['item_id']} from merchant — "
                                     f"{item['sell_price']} Marks each; "
                                     f"up to {item['sell_quantity']} offered")})
        if item["buy_quantity"]:
            result.append({"item_id": item["item_id"], "buying": False,
                           "max_quantity": min(item["buy_quantity"], MAX_TRANSACTION_QUANTITY),
                           "unit_price": item["buy_price"],
                           "label": (f"Sell {item['item_id']} to merchant — "
                                     f"{item['buy_price']} Marks each; "
                                     f"up to {item['buy_quantity']} offered")})
    return result


def copy_receipt(receipt):
    return deepcopy(receipt)
