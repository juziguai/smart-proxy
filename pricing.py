from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    label: str
    currency: str
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float
    billing_type: str = "api"
    source: str = ""


DEEPSEEK_SOURCE = (
    "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
)

MODEL_PRICING = {
    "deepseek-v4-flash": ModelPricing(
        label="deepseek-v4-flash",
        currency="CNY",
        input_per_million=1.0,
        output_per_million=2.0,
        cache_read_per_million=0.02,
        cache_write_per_million=1.0,
        source=DEEPSEEK_SOURCE,
    ),
    "deepseek-v4-pro": ModelPricing(
        label="deepseek-v4-pro",
        currency="CNY",
        input_per_million=3.0,
        output_per_million=6.0,
        cache_read_per_million=0.025,
        cache_write_per_million=3.0,
        source=DEEPSEEK_SOURCE,
    ),
}

TOKEN_PLAN_PREFIXES = (
    "MiniMax-",
    "mimo-",
)


def pricing_for_model(model):
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    if any(model.startswith(prefix) for prefix in TOKEN_PLAN_PREFIXES):
        return ModelPricing(
            label=model,
            currency="CNY",
            input_per_million=0,
            output_per_million=0,
            cache_read_per_million=0,
            cache_write_per_million=0,
            billing_type="token_plan",
        )
    return None


def estimate_usage_cost(model, usage):
    pricing = pricing_for_model(model)
    if pricing is None:
        return {
            "billable": False,
            "billing_type": "unknown",
            "currency": "CNY",
            "total": 0.0,
            "input": 0.0,
            "output": 0.0,
            "cache_read": 0.0,
            "cache_write": 0.0,
            "source": "",
        }

    input_cost = _per_million(
        usage.get("input_tokens", 0),
        pricing.input_per_million,
    )
    output_cost = _per_million(
        usage.get("output_tokens", 0),
        pricing.output_per_million,
    )
    cache_read_cost = _per_million(
        usage.get("cache_read_input_tokens", 0),
        pricing.cache_read_per_million,
    )
    cache_write_cost = _per_million(
        usage.get("cache_creation_input_tokens", 0),
        pricing.cache_write_per_million,
    )
    total = input_cost + output_cost + cache_read_cost + cache_write_cost

    return {
        "billable": pricing.billing_type == "api",
        "billing_type": pricing.billing_type,
        "currency": pricing.currency,
        "total": total,
        "input": input_cost,
        "output": output_cost,
        "cache_read": cache_read_cost,
        "cache_write": cache_write_cost,
        "source": pricing.source,
    }


def aggregate_cost(model_costs):
    total = {
        "currency": "CNY",
        "total": 0.0,
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
        "billable_models": 0,
        "token_plan_models": 0,
        "unknown_models": 0,
    }
    for cost in model_costs.values():
        total["total"] += cost["total"]
        total["input"] += cost["input"]
        total["output"] += cost["output"]
        total["cache_read"] += cost["cache_read"]
        total["cache_write"] += cost["cache_write"]
        if cost["billing_type"] == "api":
            total["billable_models"] += 1
        elif cost["billing_type"] == "token_plan":
            total["token_plan_models"] += 1
        else:
            total["unknown_models"] += 1
    return total


def _per_million(tokens, price):
    return (int(tokens or 0) / 1_000_000) * price
