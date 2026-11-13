"""Produce an accounting reconciliation report from PostgreSQL."""

import asyncio
import json
from decimal import Decimal
from typing import Any

from prism.adapters.database.session import DatabaseManager
from prism.adapters.database.tables import (
    AdmissionCounter,
    BudgetReservation,
    VirtualKey,
)
from prism.infrastructure.config import Settings
from sqlalchemy import func, select


def number(value: Decimal | None) -> str:
    return str(value or Decimal(0))


async def run() -> dict[str, Any]:
    database = DatabaseManager(Settings())
    try:
        async with database.session() as session:
            statement = (
                select(
                    VirtualKey.tenant_name,
                    VirtualKey.key_prefix,
                    AdmissionCounter.spent_usd,
                    AdmissionCounter.reserved_usd,
                    func.coalesce(
                        func.sum(BudgetReservation.settled_usd).filter(
                            BudgetReservation.status == "settled",
                            func.to_char(BudgetReservation.created_at, "YYYY-MM")
                            == AdmissionCounter.budget_month,
                        ),
                        0,
                    ).label("settled_total"),
                    func.coalesce(
                        func.sum(BudgetReservation.reserved_usd).filter(
                            BudgetReservation.status == "pending",
                            func.to_char(BudgetReservation.created_at, "YYYY-MM")
                            == AdmissionCounter.budget_month,
                        ),
                        0,
                    ).label("pending_total"),
                    func.count(BudgetReservation.id)
                    .filter(BudgetReservation.status == "reconciliation_required")
                    .label("flagged"),
                )
                .join(AdmissionCounter, AdmissionCounter.key_id == VirtualKey.id)
                .outerjoin(BudgetReservation, BudgetReservation.key_id == VirtualKey.id)
                .group_by(
                    VirtualKey.id,
                    VirtualKey.tenant_name,
                    VirtualKey.key_prefix,
                    AdmissionCounter.spent_usd,
                    AdmissionCounter.reserved_usd,
                )
                .order_by(VirtualKey.tenant_name)
            )
            rows = (await session.execute(statement)).all()
        tenants = []
        all_reconciled = True
        for row in rows:
            spent = Decimal(row[2])
            reserved = Decimal(row[3])
            settled = Decimal(row[4])
            pending = Decimal(row[5])
            reconciled = spent == settled and reserved == pending
            all_reconciled = all_reconciled and reconciled
            tenants.append(
                {
                    "tenant_name": row[0],
                    "key_prefix": row[1],
                    "counter_spent_usd": number(spent),
                    "settled_reservations_usd": number(settled),
                    "counter_reserved_usd": number(reserved),
                    "pending_reservations_usd": number(pending),
                    "reconciliation_required": int(row[6]),
                    "counter_totals_match": reconciled,
                }
            )
        return {"all_counter_totals_match": all_reconciled, "tenants": tenants}
    finally:
        await database.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
