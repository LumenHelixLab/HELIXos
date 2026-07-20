# HELIXos — Milestone 1 SPEC Addendum (binding; extends SPEC.md)

M1 = Core Foundation: FSM executor on the hardened bus + verifiable AKASH braid signatures. All M0 contracts (SPEC.md) remain in force. New module dir: `aigent-os-kernel/src/akash/` (flat import namespace; add to conftest.py).

## 1. Braid model (akash/braid.py) — owner F

A **braid** is a set of named **strands** (one per agent/writer). Each strand is a hash chain of commitments; a commitment may declare **crossings** — references to other strands' current tips — weaving a causal/topological order.

### Commitment (one per journaled instruction)
```python
@dataclass(frozen=True)
class Commitment:
    seq: int                 # global journal seq this commitment corresponds to
    strand: str              # e.g. "krishna", "natasha"
    bus_line: str            # the triangulated instruction committed
    prev: str                # previous tip hash of THIS strand (genesis: "0"*64)
    crossings: dict[str, str]  # {other_strand: tip_hash_at_commit_time}, sorted keys, may be {}
    hash: str                # sha256 hex of canonical_json({seq,strand,bus_line,prev,crossings})
```
`canonical_json` = `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

### API
```python
class Braid:
    def __init__(self): ...
    def commit(self, seq: int, strand: str, bus_line: str,
               crossings: dict[str, str] | None = None) -> Commitment
        # validates: strand nonempty [a-z0-9_-]{1,32}; bus_line matches BUS_RE;
        # crossings reference only EXISTING other strands (not self); hashes match declared tips
    def tip(self, strand: str) -> str            # current tip hash ("0"*64 if new)
    def strands(self) -> list[str]
    def root(self) -> str                        # sha256 hex of canonical_json({strand: tip} sorted)
    def to_list(self) -> list[Commitment]        # insertion order
    @classmethod
    def from_events(cls, events: list[dict]) -> "Braid"   # rebuild from journal events (see §3)

def sign_root(root: str, signer) -> str          # b64url signature over b"HELIX-BRAID/1" || bytes.fromhex(root)
def verify_root_signature(root: str, sig_b64: str, verifier) -> bool

def anchor_braid(braid: Braid, signer, backend, seq_lo: int, seq_hi: int) -> str
    # payload = compact json {"braid_root":root,"seq_lo":..,"seq_hi":..,"strands":N,"ts":..}
    # body wrapped via generate_triangulated_instruction(payload, "ARCHIVE", signer, backend)
    # returns the bus line (the "AKASH braid signature anchor")

def verify_anchor(anchor_line: str, verifier, expected_root: str, backend, expected_phase: int = 0) -> bool
    # verify_instruction_lock passes AND decoded envelope body JSON's braid_root == expected_root
```
Errors: `BraidError` (ValueError subclass) on invalid strand/crossing/chain. Deterministic: same event list → same root (tested).

## 2. Snapshots (memory/snapshot.py) — owner G
```python
def write_snapshot(path: str | Path, *, seq: int, epoch: int, fsm_state: str,
                   braid_root: str, strand_tips: dict[str, str],
                   journal_path: str, signer=None) -> dict
    # snapshot json: {"magic":"HELIXOS-SNAPSHOT/1","seq","ts","epoch","fsm_state",
    #   "braid_root","strand_tips","journal_sha256","sig"?}  (sig = sign_root-style over braid_root if signer)
    # atomic write (tmp + rename), returns the dict
def load_snapshot(path) -> dict                  # validates magic + fields, raises SnapshotError
def replay_events(journal_path: str, since_seq: int = 0) -> list[dict]
    # journal lines with seq > since_seq, chain-verified first (fail-closed: raise on bad chain)
class SnapshotError(ValueError): ...
```

## 3. FSM executor (runtime/executor.py) — owner G
Bridges bus → verification → FSM → journal → braid events.
```python
class FSMExecutor:
    def __init__(self, fsm: TenSquaredFSM, journal: EventJournal, fence: EpochFence,
                 verifier, backend, braid=None, audit=None): ...
    def apply(self, bus_line: str, strand: str = "kernel", expected_phase: int = 0) -> bool
        # 1. fence check: journal epoch == fence.current else refuse (return False, audit)
        # 2. verify_instruction_lock(bus_line, verifier, expected_phase, backend) — False → journal
        #    {"type":"instruction.rejected"} + audit + return False  (fail-closed, never raises)
        # 3. event = event_for(bus_line): if envelope body json has "event":"E[0-9]" use it,
        #    else deterministic: int(tag bytes hex[:2],16) % 10 → "E{n}"
        # 4. fsm.transition(event); seq = journal.append("fsm.transition",
        #    {"bus_line":..,"event":..,"from":..,"to":..,"strand":strand}, epoch=fence.current)
        # 5. if braid: braid.commit(seq, strand, bus_line) and journal.append("braid.commit", {...})
        # 6. return True
    def collapse(self, reason: str) -> int   # Projective Collapse: fence.increment(), journal
                                             # {"type":"epoch.collapse","reason","new_epoch"}, returns new epoch
    @classmethod
    def recover(cls, journal_path, fence, verifier, backend, snapshot_path=None, braid=None) -> "FSMExecutor"
        # load snapshot (if any) → replay_events(since_seq) → reapply fsm.transition events
        # (skip rejected) → rebuild braid from braid.commit events → return executor at recovered state
def event_for(bus_line: str) -> str          # pure, exported for tests
```
On backend Unavailable (sidecar down): apply returns False, journals `{"type":"backend.unavailable"}` — NO state change, NO exception (chaos test relies on this).

## 4. Journal event types (M1 additions to the M0 taxonomy)
`fsm.transition`, `instruction.rejected`, `backend.unavailable`, `braid.commit`, `braid.anchor`, `epoch.collapse`, `snapshot.written`. (M0 demo used free-form types; this is the canonical M1 set.)

## 5. Cauldron phase progression (M1 policy, implemented in executor + tests)
- Slot generation bumps ONLY via `bump_generation` — called by the anchor cadence process (demo: after each anchor) and MUST be called on every collapse for all live ptrs (policy documented in code).
- Consequence: post-anchor, instructions verified with the old phase fail → treated as Tampered → logged `instruction.rejected` with reason phase. This is intended forward-security (old instructions can't be re-presented after anchoring).

## 6. Quality bar (M1)
- Same as SPEC.md §4. F and G run their own pytest green. Determinism + recovery + chaos are the M1 acceptance core (docs/m1-acceptance.md in Stage 2): (a) braid root recomputable from journal alone; (b) anchor verifies against recomputed root; (c) recover() reproduces exact FSM state + braid root from snapshot+journal; (d) sidecar kill mid-stream → no state corruption, resume works; (e) 10k-instruction soak with zero chain/root mismatches.
