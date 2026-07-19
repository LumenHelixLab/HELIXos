# Runbook: Key rotation — per-agent HMAC (dev) and Ed25519 rollout (prod)

**Governing:** ADR-004 §3 (dual-mode signing), §4.4 (dev→prod migration).
Keys are env-only (SPEC §3.10): `HELIXOS_HMAC_KEY` (hex, ≥32 B) in dev;
`HELIXOS_ED25519_SK` (hex, 32 B raw seed) on the sequencer only in prod.
Keys NEVER appear in `configs/helixos.yaml`, unit files, or the journal.

## A. Scheduled HMAC rotation (dev/CI; `signing.mode: hmac_dev`)

Zero-downtime dual-verify window — verifiers accept old AND new tags while
signers cut over. Rotation is per-agent (each agent holds its own key).

1. **Generate** the new key: `python3 -c "import secrets; print(secrets.token_hex(32))"`
   (32 bytes = 64 hex chars; `HMACSigner` rejects <32 B).
2. **Stage verifiers (dual-verify):** for each verifier, run a window where
   verify tries the new key then the old key before failing:
   ```python
   ok = HMACVerifier(new_key).verify(msg, tag) or HMACVerifier(old_key).verify(msg, tag)
   ```
   Journal `key.rotation.verifier_ready` with the agent id. (M0 note: SPEC
   §3.1 ships single-key verifiers; the dual-verify wrapper is ops-side code
   for the window only, removed at step 5.)
3. **Cut signers over:** set `HELIXOS_HMAC_KEY=<new>` on the signing agent and
   restart it. New bus lines carry 22-char tags under the new key; verifiers
   still accept stragglers signed with the old key.
4. **Hold the window** ≥ `max_clock_skew_s` (300 s) so every in-flight
   old-key envelope expires out of the freshness window.
5. **Retire the old key:** remove the dual-verify fallback and the old env
   value; journal `key.rotation.complete`. Verify with the golden vectors
   (`tests/vectors/golden_vectors.json`) re-generated under the new key.
6. **Emergency rotation** (suspected compromise — remember AUD-C6: any HMAC
   verifier is also a forger): skip the window, rotate ALL agent keys at
   once, and treat all unverified in-flight instructions as `SchemaError`/
   quarantine. Prefer accelerating section B — symmetric compromise is
   total while HMAC mode is in force.

## B. Ed25519 rollout, dev → prod (ADR-004 §4.4)

1. **Develop under HMAC** (section A) — stdlib-only, fast CI.
2. **Generate the production keypair** on the sequencer host:
   ```python
   from signers import Ed25519Signer
   s = Ed25519Signer.generate()
   # persist the 32-byte seed as HELIXOS_ED25519_SK (hex) via the secrets store;
   # distribute s.public_key_bytes() (hex) to EVERY verifier — public, not secret.
   ```
3. **Distribute the public key** to all verifiers; set `HELIXOS_ED25519_SK`
   on the sequencer ONLY (verifiers hold public keys — verification no longer
   implies forgery capability, AUD-C6).
4. **Cut over by config, not code:** set `signing.mode: ed25519` in
   `configs/helixos.yaml`. `BUS_RE {22,86}` parses the mixed fleet during
   rollout — HMAC-mode lines (22-char tags) remain verifiable until step 5.
5. **Reject 22-char tags:** once all signers report Ed25519, enable the prod
   policy (ADR-004 §4.3): 22-char tags are refused and flagged in logs;
   boot fails if `HELIXOS_ED25519_SK` is unset (HMAC key ignored).
6. **Retire HMAC:** remove `HELIXOS_HMAC_KEY` from all environments; the mode
   remains for dev/CI only. Journal `key.rotation.ed25519_complete`.
7. **Sequencer key compromise in prod:** revoke by rotating the sequencer key
   and redistributing the new public key (§4.1 revocation row); no
   coordinated fleet rekey of verifier secrets is needed — there are none.
