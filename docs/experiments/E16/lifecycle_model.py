"""E16: an executable model of the forecast lifecycle (contract sections 2, 4, 8, 17, 23, 25). A small reference implementation plus a Hypothesis
stateful test (test_lifecycle.py) that applies arbitrary sequences of legal and illegal actions and checks the invariants of contract section 23
after every step. Purpose: find places where the written rules are inconsistent or leave a state undefined. Not product code.
Version 2: the first version let a token earlier than the commit be accepted and let the origin be set before the attestation window closed;
the property test found both while checking a mutant, and the model now states the rules the contract implies (CF-22, CF-23)."""
from dataclasses import dataclass, field
import os
BUG = os.environ.get("E16_BUG", "")
TERMINAL = {"SETTLED", "VOID", "UNSETTLEABLE", "FAILED"}
U = 5                 # clock uncertainty seconds (prior)
MINUTE = 60
TOKEN_WINDOW = 60     # token time must lie in [committed_at - OFFSET, committed_at + 60]
OFFSET = 1            # maximum accepted local clock offset (domain model: commits refused above 1 s)
ATTEST_DEADLINE = 90  # window plus the 30 s retry window of contract section 8

class Illegal(Exception): pass

@dataclass
class Forecast:
    horizon: int
    state: str = "CREATED"
    committed_at: int | None = None
    level: str | None = None
    attested_at: int | None = None
    attested_history: list = field(default_factory=list)
    attest_closed: bool = False
    eligible_after: int | None = None
    T0: int | None = None
    settled_at: int | None = None
    frozen_fields: tuple | None = None
    payload: dict = field(default_factory=lambda: {"p": 0.6})
    history: list = field(default_factory=list)
    token_in_window: bool = True
    closed_at: int | None = None
    n_tokens: int = 0
    n_tokens_at_close: int = 0

    def _go(self, new): self.history.append((self.state, new)); self.state = new

    def freeze_evidence(self):
        if self.state != "CREATED": raise Illegal("freeze only from CREATED")
        self._go("EVIDENCE_FROZEN")

    def decide(self):
        if self.state != "EVIDENCE_FROZEN": raise Illegal("decide only after freeze")
        self._go("DECIDED")

    def commit(self, now, level):
        if self.state != "DECIDED": raise Illegal("commit only from DECIDED")
        if level not in ("C1", "C2", "C3", "C4"): raise Illegal("bad level")
        self.committed_at = now; self.level = level
        self.frozen_fields = (self.horizon, tuple(sorted(self.payload.items())))
        self._go("COMMITTED")

    def attest(self, token_time):
        """A time-stamp token arrives. Only before the attestation window closes."""
        if self.state not in ("COMMITTED", "AWAITING_ORIGIN"): raise Illegal("attest only around commitment")
        if self.attest_closed: raise Illegal("attestation already closed")
        in_window = self.committed_at - OFFSET <= token_time <= self.committed_at + TOKEN_WINDOW
        if not in_window and BUG != "accept_any_token_time":
            return                                # a token outside the window never counts toward C3 and never moves attested_at
        new = token_time if self.attested_at is None else max(self.attested_at, token_time)
        if BUG == "attest_earlier": new = token_time
        self.attested_at = new; self.attested_history.append(new); self.n_tokens += 1
        if self.state == "COMMITTED": self._go("AWAITING_ORIGIN")

    def close_attestation(self, now):
        """Fix the level actually reached and eligible_after; allowed once the window has passed or enough tokens are in."""
        if self.state not in ("COMMITTED", "AWAITING_ORIGIN"): raise Illegal("close only around commitment")
        if self.attest_closed: raise Illegal("already closed")
        enough = self.level in ("C3", "C4") and self.n_tokens >= 2
        if now < self.committed_at + ATTEST_DEADLINE and not enough and BUG != "close_early": raise Illegal("window still open and fewer than two tokens")
        if self.level in ("C3", "C4") and self.n_tokens < 2:
            self.level = "C2"                                  # fewer than two in-window tokens: downgrade, never silently kept
        self.eligible_after = (self.attested_at + U if (self.level in ("C3", "C4") and BUG != "no_u")
                               else (self.attested_at if self.level in ("C3", "C4") else self.committed_at))
        self.attest_closed = True; self.closed_at = now; self.n_tokens_at_close = self.n_tokens
        if self.state == "COMMITTED": self._go("AWAITING_ORIGIN")

    def set_origin(self, now):
        if self.state != "AWAITING_ORIGIN": raise Illegal("origin only while awaiting")
        if not self.attest_closed and BUG != "origin_before_close": raise Illegal("attestation not closed")
        ea = self.eligible_after if self.eligible_after is not None else self.committed_at
        t0 = (ea // MINUTE + 1) * MINUTE
        if now < t0: raise Illegal("origin candle not closed yet")
        self.T0 = t0; self._go("OPEN")

    def begin_settle(self, now):
        if self.state != "OPEN": raise Illegal("settle only from OPEN")
        if now < self.T0 + self.horizon and BUG != "early_settle": raise Illegal("window not over")
        self._go("SETTLING")

    def finish(self, now, outcome):
        if self.state != "SETTLING": raise Illegal("finish only from SETTLING")
        if outcome not in ("SETTLED", "VOID", "UNSETTLEABLE"): raise Illegal("bad outcome")
        self.settled_at = now; self._go(outcome)

    def fail(self, reason):
        if (self.state in ("COMMITTED", "AWAITING_ORIGIN", "OPEN", "SETTLING") or self.state in TERMINAL) and BUG != "fail_after_commit":
            raise Illegal("cannot FAIL after commitment or when terminal")
        self._go("FAILED")

    def void_gap(self):
        if self.state in TERMINAL or self.state in ("CREATED", "EVIDENCE_FROZEN", "DECIDED"): raise Illegal("void_gap needs a committed forecast")
        self._go("VOID")

    def amend(self, **kw):
        if (self.state in ("COMMITTED", "AWAITING_ORIGIN", "OPEN", "SETTLING") or self.state in TERMINAL and self.committed_at is not None) and BUG != "amend_committed":
            raise Illegal("committed forecasts are immutable")
        self.payload.update(kw)

def check_invariants(f: Forecast):
    m = []
    if any(a in TERMINAL for a, _ in f.history): m.append("terminal state left")
    if f.state == "FAILED" and f.committed_at is not None: m.append("FAILED after COMMITTED")
    if f.committed_at is not None and f.frozen_fields != (f.horizon, tuple(sorted(f.payload.items()))): m.append("committed content changed")
    if f.T0 is not None:
        ea = f.eligible_after if f.eligible_after is not None else f.committed_at
        if not f.T0 > ea: m.append("T0 not strictly after eligible_after")
        if f.level in ("C3", "C4") and not (f.attested_at is not None and f.T0 > f.attested_at): m.append("origin not after attestation at C3 or above")
        if not f.attest_closed: m.append("origin set before the attestation window closed")
    if f.attest_closed and f.closed_at < f.committed_at + ATTEST_DEADLINE and not (f.level in ("C3", "C4") and f.n_tokens_at_close >= 2): m.append("attestation closed early without two tokens")
    if f.attest_closed and f.level in ("C3", "C4") and f.eligible_after != f.attested_at + U: m.append("eligible_after is not attested_at plus u")
    if any(b < a for a, b in zip(f.attested_history, f.attested_history[1:])): m.append("attested_at moved earlier")
    if f.state == "SETTLED" and not (f.settled_at is not None and f.settled_at >= f.T0 + f.horizon): m.append("settled before the window closed")
    if f.state in ("SETTLING", "SETTLED", "OPEN") and f.T0 is None: m.append("past origin without T0")
    if f.attest_closed and f.level in ("C3", "C4") and not (f.n_tokens_at_close >= 2 and f.attested_at is not None and f.committed_at - OFFSET <= f.attested_at <= f.committed_at + TOKEN_WINDOW):
        m.append("C3 or above without two tokens inside the window")
    if f.attested_at is not None and f.attested_at < f.committed_at - OFFSET: m.append("token earlier than the commit")
    return m
