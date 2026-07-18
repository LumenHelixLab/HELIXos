"""32.CUBIT error correction — Layer 1 packet integrity.

Wraps packets for the noisy simulated 56K-baud channels (spec §3, §4.3).

Spec: docs/SPECIFICATION.md §6 item 3.

Two codes are specified:

* **Hamming [32, 26, 4]** — systematic SECDED (single-error-correct,
  double-error-detect) for packet wrapping.
* **Extended Golay [24, 12, 8]** — corrects up to 3 errors per 24-bit word;
  used as a "damping" fiber against burst noise.

Both are standard, well-defined linear codes, but they are left as stubs here
so that the implementation lands together with a bit-exact test vector suite
(the spec's "32.CUBIT v7.7" designation implies a specific parity layout that
must be matched, not reinvented). Do not ship an unverified encoder.
"""

from __future__ import annotations

HAMMING_N, HAMMING_K, HAMMING_D = 32, 26, 4
GOLAY_N, GOLAY_K, GOLAY_D = 24, 12, 8


def hamming_encode(data: bytes) -> bytes:
    """Wrap ``data`` in systematic Hamming [32, 26, 4] SECDED codewords."""
    raise NotImplementedError(
        "Hamming [32,26,4] encoder pending 32.CUBIT v7.7 parity layout + test "
        "vectors."
    )


def hamming_decode(codeword: bytes) -> bytes:
    """Correct single-bit errors, detect double-bit errors, return payload."""
    raise NotImplementedError("See hamming_encode.")


def golay_encode(data: bytes) -> bytes:
    """Wrap ``data`` in extended Golay [24, 12, 8] codewords."""
    raise NotImplementedError(
        "Extended Golay [24,12,8] encoder pending test vectors."
    )


def golay_decode(codeword: bytes) -> bytes:
    """Correct up to 3 bit errors per 24-bit word, return payload."""
    raise NotImplementedError("See golay_encode.")
