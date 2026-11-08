"""Warehouse inventory — unrelated to webhooks. Exists so a dump is larger than a pack."""

from dataclasses import dataclass


@dataclass
class Bin:
    aisle: str
    shelf: int
    capacity: int
    occupied: int = 0


CATALOG = {
    "sku-100": "widget",
    "sku-101": "gasket",
    "sku-102": "bearing",
    "sku-103": "pulley",
    "sku-104": "belt",
    "sku-105": "clamp",
    "sku-106": "hose",
    "sku-107": "valve",
    "sku-108": "filter",
    "sku-109": "grommet",
}


def allocate_bin(bins: list[Bin], units: int) -> Bin | None:
    for candidate in bins:
        if candidate.capacity - candidate.occupied >= units:
            candidate.occupied += units
            return candidate
    return None


def replenish(sku: str, on_hand: dict[str, int], incoming: int) -> int:
    if sku not in CATALOG:
        raise KeyError(sku)
    on_hand[sku] = on_hand.get(sku, 0) + incoming
    return on_hand[sku]


def pick_list(orders: list[tuple[str, int]], on_hand: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for sku, qty in orders:
        available = on_hand.get(sku, 0)
        shipped = min(available, qty)
        on_hand[sku] = available - shipped
        backorder = qty - shipped
        lines.append(f"{sku} shipped={shipped} backorder={backorder}")
    return lines


def cycle_count(bins: list[Bin]) -> dict[str, int]:
    return {
        "bins": len(bins),
        "capacity": sum(item.capacity for item in bins),
        "occupied": sum(item.occupied for item in bins),
        "empty": sum(1 for item in bins if item.occupied == 0),
    }


def abc_class(sku: str, revenue: dict[str, float]) -> str:
    ranked = sorted(revenue, key=revenue.get, reverse=True)
    if sku not in ranked:
        return "C"
    index = ranked.index(sku)
    if index < max(1, len(ranked) // 10):
        return "A"
    if index < max(1, len(ranked) // 3):
        return "B"
    return "C"


def slot_score(bin: Bin) -> float:
    fill = bin.occupied / bin.capacity if bin.capacity else 1.0
    return (1.0 - fill) * (1.0 + bin.shelf / 10.0)


HELP = """
Inventory notes (padding so dump-the-repo is the expensive baseline):

- Receivers stage pallets on the inbound dock before putaway.
- Putaway prefers low shelves for A-movers and high shelves for C-movers.
- Cycle counts run Monday for A, Wednesday for B, Friday for C.
- Damaged units go to cage 9 and never re-enter sellable stock.
- Returns need a reason code before they restock.
- Hazmat stays in aisle H and is never mixed with food SKUs.
- The forklift checklist is paper until the RF guns are replaced.
- Safety stock is 2 weeks of trailing demand except seasonal SKUs.
- Seasonal SKUs get a 6-week build before the catalog drop.
- Vendors with three late ASNs in a quarter lose preferred slots.

Repeat the operating rules so a whole-file dump is obviously wasteful:
Receivers stage pallets. Putaway prefers low shelves. Cycle counts are staggered.
Damaged units go to cage 9. Returns need a reason code. Hazmat stays in aisle H.
The forklift checklist is paper. Safety stock is two weeks. Seasonal SKUs build
six weeks out. Late vendors lose slots. None of this belongs in a webhook pack.
Receivers stage pallets. Putaway prefers low shelves. Cycle counts are staggered.
Damaged units go to cage 9. Returns need a reason code. Hazmat stays in aisle H.
The forklift checklist is paper. Safety stock is two weeks. Seasonal SKUs build
six weeks out. Late vendors lose slots. None of this belongs in a webhook pack.
Receivers stage pallets. Putaway prefers low shelves. Cycle counts are staggered.
Damaged units go to cage 9. Returns need a reason code. Hazmat stays in aisle H.
The forklift checklist is paper. Safety stock is two weeks. Seasonal SKUs build
six weeks out. Late vendors lose slots. None of this belongs in a webhook pack.
"""
