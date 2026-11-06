"""Host-side usage normalization. Estimates are not provider invoices."""
from decimal import Decimal, ROUND_CEILING


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def _micro(value):
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def estimate(usage, pricing):
    """Rates are CNY/million tokens, so rate * tokens gives micro-CNY.

    Input includes disjoint ordinary/read/write categories. Missing cache fields
    remain unknown, not zero. Output already includes reasoning tokens.
    """
    raw = usage if isinstance(usage, dict) else {}
    inputs = _tokens(raw.get("input_tokens"))
    outputs = _tokens(raw.get("output_tokens"))
    details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    if not isinstance(output_details, dict):
        output_details = {}
    cached = _tokens(details.get("cached_tokens"))
    written = _tokens(details.get("cache_write_tokens",
                                 details.get("cache_creation_tokens",
                                             raw.get("cache_creation_input_tokens"))))
    reasoning = _tokens(output_details.get("reasoning_tokens"))
    provider_total = _tokens(raw.get('total_tokens'))
    total = provider_total if provider_total is not None else inputs+outputs if inputs is not None and outputs is not None else None
    result = {"input_tokens": inputs, "output_tokens": outputs,
              "total_tokens": total, "provider_total_tokens": provider_total,
              "total_tokens_basis": "provider" if provider_total is not None else "input_plus_output" if total is not None else "unavailable",
              "cache_read_tokens": cached, "cache_write_tokens": written,
              "reasoning_tokens": reasoning, "raw_usage": usage,
              "currency": "CNY", "price_source": pricing["source"],
              "price_checked_at": pricing.get('checked_at'),
              "rate_tier": None, "estimate_lower_micro_cny": None,
              "estimate_upper_micro_cny": None, "estimated_cny": None,
              "accounting_status": "usage_missing", "invoice_verified": False}
    if inputs is None or outputs is None:
        return result
    if ((cached or 0) + (written or 0) > inputs
            or provider_total is not None and provider_total != inputs+outputs
            or reasoning is not None and reasoning > outputs):
        result["accounting_status"] = "usage_inconsistent"
        return result
    tier = "short" if inputs <= pricing["threshold_input_tokens"] else "long"
    rates = {k: Decimal(str(v)) for k, v in pricing["member_rates"][tier].items()}
    # Known cache categories are priced once. Remaining tokens may be ordinary
    # input or one of the cache categories for which no count was returned.
    remaining = inputs - (cached or 0) - (written or 0)
    possible = [rates["input"]]
    if cached is None:
        possible.append(rates["cache_read"])
    if written is None:
        possible.append(rates["cache_write"])
    known = (rates["output"] * outputs + rates["cache_read"] * (cached or 0)
             + rates["cache_write"] * (written or 0))
    low = _micro(known + remaining * min(possible))
    high = _micro(known + remaining * max(possible))
    complete = cached is not None and written is not None
    result.update(rate_tier=tier, estimate_lower_micro_cny=low,
                  estimate_upper_micro_cny=high,
                  estimated_cny=high / 1_000_000 if complete else None,
                  accounting_status="estimated" if complete else "cache_usage_partial")
    return result


def summarize(requests):
    """Aggregate each gateway request once; absent measurements stay explicit."""
    seen = set()
    records = []
    for request in requests:
        if request["id"] in seen:
            raise ValueError("Duplicate gateway request")
        seen.add(request["id"])
        records.append(request)
    result = {"requests": len(records), "currency": "CNY",
              "invoice_verified": False, "unknown_usage_requests": 0,
              "cost_lower_cny": 0, "cost_upper_cny": 0}
    for name in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                 "cache_write_tokens", "reasoning_tokens"):
        values = [(r.get("accounting") or {}).get(name) for r in records]
        result[name] = {"known_total": sum(v for v in values if v is not None),
                        "missing_requests": sum(v is None for v in values)}
    for r in records:
        accounting = r.get("accounting") or {}
        lower = accounting.get("estimate_lower_micro_cny")
        upper = accounting.get("estimate_upper_micro_cny")
        result["cost_lower_cny"] += (lower or 0) / 1_000_000
        result["cost_upper_cny"] += (upper if upper is not None else r["reserved_micro_cny"]) / 1_000_000
        result["unknown_usage_requests"] += upper is None
    result["accounting_complete"] = all(
        (r.get("accounting") or {}).get("accounting_status") in ("estimated", "not_dispatched") for r in records)
    return result


def undispatched(pricing):
    """Host proof of no upstream request; do not invent provider usage fields."""
    value = estimate(None, pricing)
    value.update({name: 0 for name in ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_tokens',
                                      'cache_write_tokens', 'reasoning_tokens')})
    value.update(accounting_status='not_dispatched', evidence='host_no_upstream_dispatch',
                 total_tokens_basis='host_no_upstream_dispatch',
                 estimate_lower_micro_cny=0, estimate_upper_micro_cny=0, estimated_cny=0)
    return value
