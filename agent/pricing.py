"""DeepSeek Flash cost of one call, at peak rates (official pricing page, checked 16 Nov 2026).

Off-peak is half the peak rate, so this never undercounts.
"""

MISS, HIT, OUTPUT = 0.30e-6, 0.006e-6, 1.20e-6  # USD per token: input cache miss, input cache hit, output


def cost(usage) -> float:
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    miss = usage.prompt_tokens - hit if miss is None else miss
    return miss * MISS + hit * HIT + usage.completion_tokens * OUTPUT
