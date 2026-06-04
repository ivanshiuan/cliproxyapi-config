"""Pure calc engines (no DB, no HTTP, Decimal-everywhere).

Re-exports the public surface of each engine for easy importing:

    from restaurant_api.services.calc import (
        compute_daily_pnl,
        consume_bom,
        resolve_discounts,
    )
"""

from restaurant_api.services.calc.bom_consumer import (
    BOMConsumeInput,
    BOMConsumeOutput,
    MissingRecipeError,
    RecipeRow,
    StockMovementDelta,
    consume_bom,
)
from restaurant_api.services.calc.cogs_variance_detector import (
    VarianceInput,
    VarianceReport,
    detect_variance,
)
from restaurant_api.services.calc.discount_resolver import (
    DiscountResolveInput,
    DiscountResolveOutput,
    DiscountRow,
    ResolvedDiscount,
    resolve_discounts,
)
from restaurant_api.services.calc.labor_hours_classifier import (
    LaborBuckets,
    LaborInput,
    classify_hours,
)
from restaurant_api.services.calc.profit_calc import (
    CostEvent,
    DailyPnLInput,
    DailyPnLOutput,
    Discount,
    FixedCostLine,
    OrderLine,
    PlatformFee,
    compute_daily_pnl,
)
from restaurant_api.services.calc.uniform_invoice_validator import (
    TaxIdValidationInput,
    TaxIdValidationResult,
    validate_uniform_invoice,
)

__all__ = [  # noqa: RUF022 — grouped by module, not alphabetical, for readability
    # bom_consumer
    "BOMConsumeInput",
    "BOMConsumeOutput",
    "MissingRecipeError",
    "RecipeRow",
    "StockMovementDelta",
    "consume_bom",
    # discount_resolver
    "DiscountResolveInput",
    "DiscountResolveOutput",
    "DiscountRow",
    "ResolvedDiscount",
    "resolve_discounts",
    # profit_calc
    "CostEvent",
    "DailyPnLInput",
    "DailyPnLOutput",
    "Discount",
    "FixedCostLine",
    "OrderLine",
    "PlatformFee",
    "compute_daily_pnl",
    # cogs_variance_detector
    "VarianceInput",
    "VarianceReport",
    "detect_variance",
    # labor_hours_classifier
    "LaborBuckets",
    "LaborInput",
    "classify_hours",
    # uniform_invoice_validator
    "TaxIdValidationInput",
    "TaxIdValidationResult",
    "validate_uniform_invoice",
]
