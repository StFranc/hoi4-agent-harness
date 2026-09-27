"""Counting a brief's tokens, and saying how it was counted.

Brief size is the number the cost story rests on, so the transcript records it
per turn rather than leaving it to be estimated afterwards. A real tokenizer is
used when one is importable and loads (``tiktoken``, the ``o200k_base``
encoding); otherwise the count falls back to characters / 4 and the transcript
records that it did. A measurement is only as good as its stated method, so the
method travels with every number.

Tokenizers differ between vendors by tens of percent on the same text, so even
the real count is one vendor's count. The provider's own ``input_tokens`` in
each ``llm`` record is the authoritative figure for what a call was billed.
"""

from __future__ import annotations

_ENCODER = None
_TRIED = False


def _encoder():
    global _ENCODER, _TRIED
    if not _TRIED:
        _TRIED = True
        try:
            import tiktoken

            _ENCODER = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001 - missing package, or the encoding cannot be fetched
            _ENCODER = None
    return _ENCODER


def count_tokens(text: str) -> tuple[int, str]:
    """``(tokens, method)``; method is ``"tiktoken:o200k_base"`` or ``"chars/4"``."""
    encoder = _encoder()
    if encoder is not None:
        return len(encoder.encode(text)), "tiktoken:o200k_base"
    return (len(text) + 3) // 4, "chars/4"
