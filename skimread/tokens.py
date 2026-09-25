"""Cheap, tokenizer-free token estimation.

We deliberately avoid shipping a BPE vocabulary. Modern tokenizers land at
roughly 3.5-4 characters per token on code and terminal output; we use 3.6 so
estimates err slightly high. Ratios between two estimates (the numbers that
matter for "how much did we save") are unaffected by the constant.
"""

CHARS_PER_TOKEN = 3.6


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN + 0.5))


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_bytes(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f} KB"
    return f"{n} B"
