"""Indian equity transaction cost calculator.

Computes all statutory charges for NSE/BSE equity trades:
  - Brokerage  : min(flat_fee, pct_of_turnover)
  - STT        : Securities Transaction Tax
  - Exchange   : NSE/BSE transaction charge
  - SEBI fee   : Regulatory fee
  - Stamp duty : State government levy (buy-side only)
  - GST        : 18% on (brokerage + exchange + SEBI fee)

Rates are current as of FY 2024-25 for equity segment.
NFO / MCX have different rates and are out of scope here.
"""

from __future__ import annotations


class ChargesCalculator:
    """Stateless helper that computes total charges for one fill."""

    # ── NSE/BSE equity rates ───────────────────────────────────────────────────
    _EXCHANGE_CHARGE_RATE = 0.0000345   # 0.00345% of turnover
    _SEBI_FEE_RATE        = 0.000001    # ₹10 per crore → 1e-6 of turnover
    _GST_RATE             = 0.18        # 18% on (brokerage + exchange + SEBI)

    # STT (Securities Transaction Tax) — equity segment
    _STT_DELIVERY_SELL    = 0.001       # 0.1% on sell-side (CNC / NRML delivery)
    _STT_INTRADAY         = 0.00025     # 0.025% on both sides (MIS)

    # Stamp duty — buy-side only
    _STAMP_DELIVERY_BUY   = 0.00015    # 0.015% for delivery
    _STAMP_INTRADAY_BUY   = 0.00003    # 0.003% for intraday

    @classmethod
    def compute(
        cls,
        product: str,
        side: str,
        quantity: int,
        price: float,
        commission_flat: float = 0.0,
        commission_pct: float = 0.0,
    ) -> float:
        """Return total charges in INR for a single fill.

        Args:
            product:        "MIS" | "NRML" | "CNC"
            side:           "BUY" | "SELL"
            quantity:       Number of shares filled.
            price:          Fill price in INR.
            commission_flat: Flat brokerage per trade in INR (e.g. 20.0).
            commission_pct:  Percentage brokerage as a fraction (e.g. 0.0003).

        Returns:
            Total charges in INR (always ≥ 0).
        """
        if quantity <= 0 or price <= 0:
            return 0.0

        turnover = quantity * price

        # ── Brokerage ─────────────────────────────────────────────────────────
        if commission_flat > 0 or commission_pct > 0:
            pct_charge = commission_pct * turnover if commission_pct > 0 else float("inf")
            flat_charge = commission_flat if commission_flat > 0 else float("inf")
            brokerage = min(pct_charge, flat_charge)
        else:
            brokerage = 0.0

        # ── STT ───────────────────────────────────────────────────────────────
        is_intraday = product.upper() == "MIS"
        if is_intraday:
            stt = cls._STT_INTRADAY * turnover
        else:
            # Delivery: STT only on sell side
            stt = cls._STT_DELIVERY_SELL * turnover if side.upper() == "SELL" else 0.0

        # ── Exchange transaction charge ────────────────────────────────────────
        exchange_charge = cls._EXCHANGE_CHARGE_RATE * turnover

        # ── SEBI regulatory fee ────────────────────────────────────────────────
        sebi_fee = cls._SEBI_FEE_RATE * turnover

        # ── Stamp duty (buy-side only) ─────────────────────────────────────────
        if side.upper() == "BUY":
            stamp_duty = (
                cls._STAMP_INTRADAY_BUY if is_intraday else cls._STAMP_DELIVERY_BUY
            ) * turnover
        else:
            stamp_duty = 0.0

        # ── GST (18% on brokerage + exchange + SEBI fee) ─────────────────────
        gst = cls._GST_RATE * (brokerage + exchange_charge + sebi_fee)

        total = brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst
        return round(total, 4)
