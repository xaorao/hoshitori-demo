import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize, precondition
from lifecycle_model import *

REACHED = {}

class M(RuleBasedStateMachine):
    """Forward-progress rules carry preconditions so that exploration reaches deep states; 'poke' rules apply any action at any time and must
    be rejected without changing state; destructive rules (fail, void_gap) are enabled only on a minority of runs."""
    @initialize(h=st.sampled_from([3600, 14400, 86400]), destructive=st.sampled_from([False, False, False, True]))
    def init(self, h, destructive):
        self.f = Forecast(horizon=h); self.now = 1000; self.destructive = destructive; self.illegal = 0
    def _try(self, fn, *a, **kw):
        before = (self.f.state, self.f.T0, self.f.attested_at, self.f.eligible_after, tuple(self.f.history), tuple(sorted(self.f.payload.items())))
        try: fn(*a, **kw)
        except Illegal:
            self.illegal += 1
            assert before == (self.f.state, self.f.T0, self.f.attested_at, self.f.eligible_after, tuple(self.f.history), tuple(sorted(self.f.payload.items()))), "an illegal action changed state"
    @rule(dt=st.integers(0, 100000))
    def tick(self, dt): self.now += dt
    @precondition(lambda s: s.f.state == "CREATED")
    @rule()
    def freeze(self): self._try(self.f.freeze_evidence)
    @precondition(lambda s: s.f.state == "EVIDENCE_FROZEN")
    @rule()
    def decide(self): self._try(self.f.decide)
    @precondition(lambda s: s.f.state == "DECIDED")
    @rule(level=st.sampled_from(["C1", "C2", "C3", "C4"]))
    def commit(self, level): self._try(self.f.commit, self.now, level)
    @precondition(lambda s: s.f.state in ("COMMITTED", "AWAITING_ORIGIN"))
    @rule(delay=st.integers(-80, 300))
    def attest(self, delay): self._try(self.f.attest, self.now + delay)
    @precondition(lambda s: s.f.state in ("COMMITTED", "AWAITING_ORIGIN") and not s.f.attest_closed)
    @rule()
    def close(self): self._try(self.f.close_attestation, self.now)
    @precondition(lambda s: s.f.state == "AWAITING_ORIGIN")
    @rule()
    def origin(self): self._try(self.f.set_origin, self.now)
    @precondition(lambda s: s.f.state == "OPEN")
    @rule()
    def settle(self): self._try(self.f.begin_settle, self.now)
    @precondition(lambda s: s.f.state == "SETTLING")
    @rule(o=st.sampled_from(["SETTLED", "SETTLED", "SETTLED", "VOID", "UNSETTLEABLE"]))
    def finish(self, o): self._try(self.f.finish, self.now, o)
    @precondition(lambda s: s.destructive)
    @rule()
    def fail(self): self._try(self.f.fail, "r")
    @precondition(lambda s: s.destructive)
    @rule()
    def gap(self): self._try(self.f.void_gap)
    # pokes: any action at any time
    @rule(which=st.sampled_from(["freeze", "decide", "commit", "attest", "origin", "settle", "finish", "amend", "fail", "close"]), x=st.integers(-100, 100000))
    def poke(self, which, x):
        if which in ("fail",) and not self.destructive: return
        f = self.f
        {"freeze": lambda: self._try(f.freeze_evidence), "decide": lambda: self._try(f.decide), "commit": lambda: self._try(f.commit, self.now, "C3"),
         "attest": lambda: self._try(f.attest, self.now + x), "origin": lambda: self._try(f.set_origin, self.now), "settle": lambda: self._try(f.begin_settle, self.now),
         "finish": lambda: self._try(f.finish, self.now, "SETTLED"), "amend": lambda: self._try(f.amend, p=0.123), "fail": lambda: self._try(f.fail, "r"), "close": lambda: self._try(f.close_attestation, self.now)}[which]()
    @invariant()
    def hold(self):
        REACHED[self.f.state] = REACHED.get(self.f.state, 0) + 1
        m = check_invariants(self.f)
        assert not m, m

TestLifecycle = M.TestCase
TestLifecycle.settings = settings(max_examples=2500, stateful_step_count=70, deadline=None, database=None, derandomize=True)

def teardown_module(module):
    json.dump(REACHED, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reached_states.json"), "w"), indent=1)
