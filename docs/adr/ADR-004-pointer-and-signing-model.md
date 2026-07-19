# ADR-004: Pointer Model (64-bit Write-Once + Generation Counters) and Dual-Mode Signing (HMAC-dev / Ed25519-prod)

| | |
|---|---|
| **Status** | Accepted |
| **Date** | M0 hardening pass |
| **Audit finding(s) resolved** | **AUD-H2** (pointer aliasing + reset-flood DoS), **AUD-C6** (symmetric-key forgery), **AUD-H1** (48-bit tags), **AUD-C5** (replay); contributes to AUD-C4 (unbound axes), AUD-H3 (hardcoded phase) |
| **Governs** | `knotcore_sim.py` (SPEC §3.2), `signers.py` (SPEC §3.1), `KNOT_API_WRAPPER.py` (SPEC §3.3), wire format (SPEC §2) |

---

## 1. Context

Two coupled defects in the v2.0 design are resolved together because they meet at the
bus line `[ ptr | verb | #tag ]`: the pointer names the state, the tag authenticates it.

**Pointers (AUD-H2).** v2.0 specified a **1-byte TinyPointer (256 slots)** while
simultaneously advising that addresses be treated as "immutable braid signatures".
On wraparound, a new store reuses a slot: **victim pointers silently alias attacker
payloads**, completing AUD-C4's unauthenticated-swap chain. Worse, the same small
space enabled a **reset-flood DoS with no key at all**: flood 256 writes → every
in-flight instruction fails verification → v2.0 mandated a "Projective Collapse" per
failure → total denial of service for the cost of 256 store calls.

**Signing (AUD-C6, AUD-H1, AUD-C5).** v2.0's tag was 8 base64url chars (~48 bits) of a
**symmetric** HMAC over **only the payload**, with **no nonce or timestamp**:

- AUD-H1: 48-bit tags collide by birthday bound at ~2²⁴ stored instructions — and any
  agent can call `store_instruction`, so chosen-payload tag aliasing is practical.
- AUD-C6: every verifier holds the key that *creates* — one compromised or
  prompt-injected agent is total, undetectable forgery, and revocation means a
  coordinated global rekey. The audit called asymmetric signing the **highest-leverage
  architectural fix** in the report.
- AUD-C5: a deterministic MAC with no freshness input replays forever.
- AUD-C4: pointer and verb were outside the MAC entirely (byte-identical tags for
  `READ` vs `EXEC` on the same payload, verified by execution).

SPEC §2 and §3.1 already fix the wire format and signer API; this ADR records **why**
the model is what it is and what each mode guarantees.

## 2. Decision 1 — Pointer model

**TinyPointers are 64-bit, write-once slot handles with per-slot generation counters.**

| Property | Specification |
|---|---|
| Wire form | 16 lowercase hex chars (`[0-9a-f]{16}`) in `BUS_RE` (SPEC §2) |
| Address space | 2⁶⁴ pointers; default store capacity 2¹⁶ = 65,536 slots (`configure(capacity)` in `knotcore_sim.py`) |
| Write discipline | **Write-once**: a slot, once allocated, is never overwritten or reallocated to a different payload |
| Exhaustion | `store_instruction` raises **`StoreFull`** (a typed `RuntimeError`) — capacity failure is explicit, never a silent wrap |
| Generations | Each slot carries a monotonically increasing generation counter starting at 0. `verify_cauldron_phase(ptr, tag, phase)` passes iff the pointer exists **and** `phase == current generation` |
| Invalidation | `bump_generation(ptr)` advances the generation, atomically invalidating all tags verified against earlier phases — the sanctioned, rate-bounded replacement for "reset" |

Why this kills the AUD-H2 attack pair:

1. **Aliasing**: write-once + 2⁶⁴ space means a pointer can never come to name a
   different payload within any feasible lifetime. The chosen-payload aliasing attack
   requires overwrite; overwrite no longer exists.
2. **Reset-flood DoS**: there is no wraparound to flood; exhaustion raises `StoreFull`
   at 2¹⁶ writes, a loud, typed failure (classified `Unavailable`/capacity per
   ADR-001 §2.4) rather than 65,536 silent invalidations. Generation bumps are the only
   invalidation path and are an explicit privileged call, not a side effect of storing.

## 3. Decision 2 — Dual-mode signing

**Two signing modes share one canonical signed input and one wire grammar, selected by
configuration** (SPEC §3.1, §3.10):

### 3.1 Canonical signed input (both modes; resolves AUD-C4)

```
b"HELIX-BUS/2" ‖ for each of (ptr bytes, verb utf-8, stored-envelope utf-8):
                   4-byte big-endian length ‖ bytes
```

The length-prefixed encoding binds **all three axes** — pointer, verb, and stored
payload — and is immune to delimiter injection. Verb is restricted to
`ALLOWED_VERBS = {READ, WRITE, EXEC, ARCHIVE}` at generation (AUD-M1).

### 3.2 DEV mode — HMAC-SHA256 truncated to 16 bytes

| Property | Specification |
|---|---|
| Tag | 16 bytes (128 bits) of HMAC-SHA256 → **22 base64url chars** (no padding) |
| Verify | Recompute + `hmac.compare_digest` (constant-time) — truncation is sound here because the verifier *recomputes* the MAC |
| Keys | Per-agent keys, ≥32 bytes (`ValueError` otherwise), from env (`HELIXOS_HMAC_KEY`), with documented rotation |
| Use | **Development and CI only.** Every verifier is also a forger (symmetric); acceptable only where all key holders are trusted by definition |

128-bit tags move the birthday bound to ~2⁶⁴ stored instructions (from 2²⁴ at 48 bits)
— AUD-H1 closed with margin, while the bus line stays short.

### 3.3 PRODUCTION mode — Ed25519, full-signature tags

| Property | Specification |
|---|---|
| Tag | **Full 64-byte Ed25519 signature** → **86 base64url chars** (no padding) |
| Verify | `cryptography` library `verify` against the **public key only**; 32-byte raw seed from env (`HELIXOS_ED25519_SK`) |
| Keys | Only the sequencer holds the private key; **verifiers hold public keys** — verification capability no longer implies forgery capability (AUD-C6) |

**Why the wire tag must carry the full signature.** An Ed25519 signature is the pair
R(32B)‖S(32B), and the verification equation needs *both* in full. Unlike HMAC, a
verifier **cannot recompute the signature without the private key**, so the
"truncate and compare the prefix" trick used in DEV mode is mathematically unavailable:
a 16-byte prefix of a signature is simply unverifiable — not weaker, *unverifiable*.
(SPEC §3.1 records this as the discovered resolution of the truncated-Ed25519 idea.)
The production wire tag therefore carries the full 64-byte signature.

### 3.4 One grammar, two tag classes

`BUS_RE`'s tag group admits both lengths:

```
#(?P<tag>[A-Za-z0-9_-]{22,86})
```

`signers.py` exposes `tag_length` per mode; the wrapper formats and classifies
accordingly. A 22-char tag is HMAC-mode; an 86-char tag is Ed25519-mode; anything else
fails the regex and is rejected before any cryptography runs (fail-closed per
SPEC §3.3: `verify_instruction_lock` returns `False` on any exception, never raises).

### 3.5 Freshness envelope (resolves AUD-C5)

Freshness rides **inside the stored payload**, preserving the 3-axis wire format:

```json
{"ts": <int epoch seconds>, "nonce": <b64url, 12 random bytes>, "body": <payload_text>}
```

Verification enforces both halves:

1. **Skew window:** `|now − ts| ≤ 300 s` (`MAX_CLOCK_SKEW_S`); stale envelopes fail.
2. **Seen-nonce cache:** a nonce already verified inside the window fails as a replay;
   entries expire with the window, bounding cache size.

Because `ts` and `nonce` are inside the envelope, and the envelope is inside the
canonical signed input (§3.1), freshness is itself authenticated — an attacker cannot
refresh a stale instruction's timestamp without invalidating the tag.

The caller-supplied `expected_phase` (SPEC §3.3) closes AUD-H3's hardcoded `phase=0`:
generation progression (§2) becomes a real anti-replay axis rather than a frozen
constant.

## 4. Consequences

### 4.1 What each mode guarantees

| Guarantee | DEV (HMAC-16B / 22-char tag) | PROD (Ed25519-64B / 86-char tag) |
|---|---|---|
| Integrity / tamper-evidence over ptr+verb+payload | Yes (128-bit tag) | Yes (full signature) |
| Verifier cannot forge | **No** — symmetric key | **Yes** — public key only |
| Compromise of one agent | Total forgery; global rekey | Cannot forge sequencer signatures |
| Revocation | Rotate all per-agent keys | Rotate sequencer key; redistribute public key |
| Replay protection | ts + nonce + 300 s window + seen-cache | Same |
| Pointer aliasing | None (write-once, 2⁶⁴) — orthogonal layer | Same |
| Wire length | Short bus line | +64 chars; still far inside the 512-byte IRC line |
| Dependency | stdlib only | `cryptography` package |
| Trust domain | Dev/CI, all insiders trusted | Untrusted-agent fleet |

### 4.2 Positive

- **AUD-C6's "highest-leverage fix" lands**: production verifiers hold only public
  keys; a prompt-injected agent cannot mint instructions.
- **AUD-H2's DoS and aliasing are removed structurally**, not by rate-limit patches:
  no overwrite exists to exploit, and exhaustion is a typed error.
- **One wire grammar serves both modes** (`{22,86}`), so dev artifacts and prod
  artifacts are parse-compatible; tooling never forks.
- **Freshness is authenticated and cheap**: no extra wire fields, no protocol change.
- **Generation counters turn phase into a first-class invalidation axis**, giving
  `bump_generation` semantics a clean home in the store ABI (SPEC §3.2).

### 4.3 Negative

- **Two modes = two failure postures to reason about.** Mitigated by policy: HMAC mode
  is refused in production configuration (boot-time check: `HELIXOS_ED25519_SK`
  required, HMAC key ignored), and 22-char tags are flagged in production logs.
- **86-char tags lengthen every production bus line** (~40% longer lines on typical
  instructions). Accepted: still one IRC line; the alternative (truncated Ed25519) is
  unverifiable, not shorter-but-weaker (§3.3).
- **Key-management debt remains real**: rotation is documented but manual in M0–M1;
  a KMS is deferred substrate work (AUD-H8), not claimed here.
- **Store capacity is finite by design** (2¹⁶ default): capacity planning and
  `StoreFull` handling are now operational concerns; `configure(capacity)` and
  snapshot/archival (ADR-001) are the levers.
- **Seen-nonce cache is in-memory**: a verifier restart within the 300 s window
  re-exposes a narrow replay slot; acceptable for M1 (collapse-and-rebuild paths in
  ADR-001 re-fence epochs on restart), revisited if verifiers become long-lived
  services.

### 4.4 Migration path, dev → prod

1. Develop and test under HMAC mode (stdlib-only, fast CI, per-agent keys).
2. Before production: generate the Ed25519 keypair; distribute the **public** key to
   all verifiers; set `HELIXOS_ED25519_SK` on the sequencer only.
3. Cutover is a config flag, not a code change: `signers.py` selects the mode;
   `BUS_RE {22,86}` parses the mixed fleet during rollout; policy rejects 22-char tags
   once cut over.
4. Post-cutover, HMAC keys are retired and their env vars removed; the mode remains
   for dev/CI only.

## 5. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Truncated Ed25519 tag (16 B)** | Unverifiable in principle — verification requires full R‖S and cannot be recomputed without the private key (§3.3) |
| **HMAC-only everywhere, keys under KMS** | Per-agent keys still make every verifier a forger *within its own key*; KMS adds infra the M0 stdlib constraint excludes; kept as DEV mode only |
| **ECDSA P-256 / RSA signatures** | Larger or slower with no advantage over Ed25519 (deterministic, 64-byte sigs, 32-byte keys, mature `cryptography` support) |
| **128-bit pointers / UUIDs** | 16 extra hex chars per line for zero threat-model gain; 2⁶⁴ write-once already makes aliasing infeasible |
| **Content-hash pointers (hash-of-payload)** | Couples store addressing to payload content, breaks write-once slot semantics and generation counters, and leaks payload equality across instructions |
| **Freshness on the wire (4th axis)** | Changes the 3-axis grammar every tool parses; embedding ts+nonce in the signed envelope achieves the same binding with zero wire change |

## 6. Audit finding(s) resolved

- **AUD-H2** — write-once 64-bit pointers, `StoreFull`, generation counters (§2).
- **AUD-C6** — production Ed25519 with public-key-only verifiers (§3.3, §4.4).
- **AUD-H1** — 128-bit HMAC tags in dev; full signatures in prod (§3.2, §3.3).
- **AUD-C5** — authenticated freshness envelope + skew window + seen-nonce cache (§3.5).
- **AUD-C4** — canonical length-prefixed input binds ptr, verb, payload (§3.1).
- **AUD-H3** — caller-supplied `expected_phase` against live generation counters (§3.5).
