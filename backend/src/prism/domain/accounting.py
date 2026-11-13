"""Money-safe cost estimation and accounting value objects."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

MONEY_QUANTUM = Decimal("0.0000000001")
TOKENS_PER_PRICE_UNIT = Decimal(1000)


def money(value: Decimal) -> Decimal:
    """Round conservatively to the database's ten-decimal monetary scale."""

    return value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


@dataclass(frozen=True, slots=True)
class Pricing:
    input_cost_per_1k: Decimal
    output_cost_per_1k: Decimal

    def estimate(self, input_tokens: int, maximum_output_tokens: int) -> Decimal:
        if input_tokens < 0 or maximum_output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        value = (
            Decimal(input_tokens) * self.input_cost_per_1k
            + Decimal(maximum_output_tokens) * self.output_cost_per_1k
        ) / TOKENS_PER_PRICE_UNIT
        return money(value)


@dataclass(frozen=True, slots=True)
class Admission:
    request_log_id: UUID
    reservation_id: UUID
    reserved_usd: Decimal


@dataclass(frozen=True, slots=True)
class Settlement:
    reservation_id: UUID
    status: str
    settled_usd: Decimal | None
    changed: bool
