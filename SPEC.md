# HELIXos — Milestone 0 SPEC (single source of truth)

M0 hardening pass per `../HELIXos_Handoff_Audit.md` §7. Everything here is binding. Python 3.11+ (env: 3.12), `cryptography` 44.x and `pytest` 9.x available; otherwise **stdlib only** (no msgpack — use newline-delimited JSON).

## 1. Directory layout & module names (flat import namespace)

All code dirs are on `sys.path` via root `conftest.py`. Module filenames are unique; import by bare module name (e.g. `from signers import HMACSigner`).

```
HELIXos/
├── conftest.py                      # sys.path setup (owner: E)
├── pyproject.toml                   # owner: E
├── docs/                            # owners: A, B
├── aigent-os-kernel/
│   ├── src/
│   │   ├── runtime/ten_squared_fsm.py      # owner: D
│   │   ├── memory/journal.py  epochs.py    # owner: D
│   │   └── BABEL/dispatcher.py             # owner: D
│   ├── KNOTstore_bin/
│   │   ├── signers.py  knotcore_sim.py  KNOT_API_WRAPPER.py   # owner: C
│   │   └── sidecar/{rpc_protocol.py, sidecar_server.py, sidecar_client.py}  # owner: C
│   └── orchestrator/possession.py          # owner: D
├── tests/                           # C and D write their own test_*.py (unique names)
│   └── vectors/golden_vectors.json         # owner: C
├── configs/helixos.yaml  configs/helixos.schema.json   # owner: E
├── infra/systemd/*.service  infra/runbooks/*.md        # owner: E
├── scripts/demo_m0.py               # owner: Stage-2 integrator
├── HELIXvault/  Obsidian-Brain/     # skeleton READMEs, owner: E
└── .github/workflows/ci.yml         # owner: E
```

## 2. Wire format (unchanged from audit §6, pointer widened)

`[ {ptr} | {verb} | #{tag} ]` — ptr: 16 lowercase hex chars (64-bit); verb: `[A-Z0-9_]{1,16}`; tag: 22 base64url chars (16 bytes, no padding).

```python
BUS_RE = re.compile(r"^\[ (?P<ptr>[0-9a-f]{16}) \| (?P<verb>[A-Z0-9_]{1,16}) \| #(?P<tag>[A-Za-z0-9_-]{22}) \]$")
ALLOWED_VERBS = frozenset({"READ", "WRITE", "EXEC", "ARCHIVE"})
TAG_BYTES = 16
MAX_CLOCK_SKEW_S = 300
```

Canonical MAC/signature input: `b"HELIX-BUS/2"` + for each of (ptr bytes, verb utf-8, stored-envelope utf-8): 4-byte big-endian length + bytes. Freshness envelope stored in KNOTstore: compact JSON `{"ts":int,"nonce":b64url(12B),"body":payload_text}`.

## 3. Interface contracts

### 3.1 signers.py (C)
```python
class HMACSigner:                        # dev mode
    def __init__(self, key: bytes): ...  # >=32 bytes else ValueError
    def sign(self, msg: bytes) -> bytes: ...        # 16-byte tag (SHA-256 truncated)
class HMACVerifier:
    def __init__(self, key: bytes): ...
    def verify(self, msg: bytes, tag: bytes) -> bool: ...   # hmac.compare_digest
class Ed25519Signer:                       # production (cryptography pkg)
    def __init__(self, private_key_bytes: bytes): ...       # 32-byte raw seed
    @classmethod
    def generate(cls) -> "Ed25519Signer": ...
    def public_key_bytes(self) -> bytes: ...
    def sign(self, msg: bytes) -> bytes: ...                # 64-byte sig truncated to 16
class Ed25519Verifier:
    def __init__(self, public_key_bytes: bytes): ...
    def verify(self, msg: bytes, tag: bytes) -> bool: ...
```
NOTE: Ed25519 truncated-signature verify cannot use the library verifier (truncation breaks it). Implement `Ed25519Verifier.verify` by **re-deriving**: an Ed25519 sig is R(32B)‖S(32B); deterministic per (key,msg). Verify by signing-equivalent check is impossible without the private key — SO instead: Ed25519Verifier holds the full 64-byte signature expectation pattern: tag = first 16 bytes of sig is NOT verifiable. **Resolution:** for Ed25519 mode the wire tag carries the FULL signature base64url'd (86 chars) and BUS_RE tag group becomes `[A-Za-z0-9_-]{22,86}`; verifier uses the library `verify`. HMAC mode uses 22-char tags. `signers.py` exposes `tag_length` per mode; wrapper formats accordingly. Document this in ADR-004 (owner B reads this spec).

### 3.2 knotcore_sim.py (C) — reference simulator for the proprietary blackbox
64-bit write-once slots with generation counters. Module-level API mirroring the blackbox ABI:
```python
def store_instruction(payload: str) -> str        # -> 16-hex ptr; raises StoreFull
def fetch_payload(ptr: str) -> str | None
def verify_cauldron_phase(ptr: str, tag: str, phase: int) -> bool  # ptr exists AND phase == current generation-phase of slot
class StoreFull(RuntimeError): ...
def reset_store() -> None                          # tests only
```
Phases: each slot has a monotonically increasing generation (starts 0). `bump_generation(ptr)` advances it (invalidating older tags' phase checks). Capacity default 2**16 slots (configurable ctor arg via `configure(capacity)`); exhaustion raises StoreFull.

### 3.3 KNOT_API_WRAPPER.py (C) — corrected per audit §6.1, adapted
```python
class KnotBackend(Protocol):
    def store_instruction(self, payload: str) -> str: ...
    def fetch_payload(self, ptr: str) -> str | None: ...
    def verify_cauldron_phase(self, ptr: str, tag: str, phase: int) -> bool: ...
class SimBackend:        # wraps knotcore_sim module functions
class SidecarBackend:    # wraps sidecar_client.KnotClient  (C)
def generate_triangulated_instruction(payload_text: str, action_verb_unicode: str,
                                      signer, backend: KnotBackend | None = None) -> str
def verify_instruction_lock(instruction: str, verifier, expected_phase: int = 0,
                            backend: KnotBackend | None = None) -> bool   # fail-closed, never raises
```
Default backend = SimBackend. Fail-closed: verify returns False on ANY exception (logged). Replay protection: seen-nonce cache + 300s skew window. Generation validates ptr format and raises on invalid inputs (dev-visible), verify never raises.

### 3.4 sidecar (C)
- `rpc_protocol.py`: newline-delimited JSON frames `{"id":int,"method":str,"params":dict}` → `{"id":int,"result":...} or {"id":int,"error":str}`. `encode_request/encode_response/parse_frame`, 1 MiB frame cap.
- `sidecar_server.py`: Unix-socket server (default `$HELIXOS_SIDECAR_SOCKET` or `/tmp/helixos-knotcore.sock`), threading per connection, wraps knotcore_sim (or real .so later via same ABI), methods: `store_instruction`, `fetch_payload`, `verify_cauldron_phase`, `health` → `{"status":"ok","abi_version":1}`, `abi_version`. Graceful SIGTERM. `main()` entry.
- `sidecar_client.py`: `class KnotClient(socket_path, timeout=2.0, breaker_threshold=5, breaker_reset_s=30)` with the three methods + `health()`. Per-call deadline (socket timeout); circuit breaker: after `breaker_threshold` consecutive errors → open → calls raise `Unavailable` immediately until `breaker_reset_s` elapses → half-open. `class Unavailable(RuntimeError)`.

### 3.5 dispatcher.py (D) — src/BABEL/
```python
class BabelDispatcher:
    def __init__(self, audit: Callable[[str], None] | None = None): ...
    def register(self, verb: str, handler: Callable[[list[str]], object]) -> None
    def dispatch_direct(self, command: str) -> object   # validate → audit → execute
```
Validation: str, non-empty, ≤400 bytes utf-8, no `\r\n\x00`; first token = verb (uppercased) must be registered else `UnknownCommand`. `dispatch_direct` audits `babel.dispatch cmd=%r` (or via injected audit). Exceptions: `CommandError`, `UnknownCommand(CommandError)`.

### 3.6 possession.py (D) — corrected per audit §6.2 (adopt verbatim, adapted imports)
`KrishnaManifestor(agent_id, owner_token_hash: bytes, dispatcher, audit=None)` — dispatcher is any object with `.dispatch_direct(str)`. Token from env `HELIXOS_OWNER_TOKEN` hashed via `KrishnaManifestor.hash_token`. Keep: rate limit 5/300s, `threading.Lock`, KRISHNA-only construction, `PossessionDenied` on bad token/unpossessed manifest, CRLF/NUL/400B command validation, audit every transition. ADD fencing: `self.fencing_token: int` incremented on every possess transition and included in audit record.

### 3.7 journal.py (D) — src/memory/
Append-only event-sourced journal, JSON lines, opened `O_APPEND|O_CREAT`, fsync each append.
```python
class EventJournal:
    def __init__(self, path: str | Path): ...
    def append(self, event_type: str, payload: dict, epoch: int = 0) -> int   # -> seq
    def read_all(self) -> list[dict]
    def verify_chain(self) -> bool      # sha256 hash-chain integrity
```
Line: `{"seq":int,"ts":float,"epoch":int,"type":str,"payload":dict,"prev":hex64,"hash":hex64}` where hash = sha256(canonical JSON of all fields except hash). Single-writer: `fcntl.flock` exclusive on append (advisory; documented).

### 3.8 epochs.py (D)
```python
class EpochFence:
    def __init__(self, epoch: int = 0): ...
    @property
    def current(self) -> int
    def increment(self) -> int           # returns new epoch (Projective Collapse trigger)
    def fences(self, epoch: int) -> bool # True if epoch < current (stale, must be fenced)
```

### 3.9 ten_squared_fsm.py (D) — src/runtime/
TEN-SQUARED = 100-state FSM (10×10 grid, states `S00`..`S99`). Explicit transition table: rows = current state, 10 events `E0..E9`; deterministic rule `S(r,c) --Ee--> S(r', c')` where `r' = (r + e) % 10`, `c' = (c * 3 + e + r) % 10` (documented as the reference rule; table materialized at init as tuple-of-tuples, no dict lookups on hot path).
```python
class TenSquaredFSM:
    STATES: tuple  # 100 state names
    def __init__(self, start: str = "S00"): ...
    def transition(self, event: str) -> str        # no allocation on hot path
    @property
    def state(self) -> str
def benchmark(iterations: int = 100_000) -> dict   # {"p50_us":..,"p99_us":..,"p999_us":..} per transition
```
Benchmark uses `time.perf_counter_ns`, pre-created event list, `gc.disable()` during measurement, reports percentiles. Target: p99 < 1000 µs (in-process budget per docs/latency-budgets.md).

### 3.10 Config & env (E documents in configs/)
Env vars: `HELIXOS_HMAC_KEY` (hex ≥32B), `HELIXOS_ED25519_SK` (hex 32B seed), `HELIXOS_OWNER_TOKEN`, `HELIXOS_JOURNAL_PATH` (default `./helixos-journal.jsonl`), `HELIXOS_SIDECAR_SOCKET` (default `/tmp/helixos-knotcore.sock`), `HELIXOS_LOG_LEVEL` (default INFO).

## 4. Quality bar
- Every module: stdlib logging, type hints, module docstring stating which audit finding(s) it fixes.
- C and D MUST run `pytest tests/test_<their>*.py` green before finishing.
- No placeholders, no TODOs, no dead code. Secrets only from env.
