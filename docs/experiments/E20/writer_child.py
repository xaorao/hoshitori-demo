import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_writer as L
d, mode, crash_at, crash_after, n, start = sys.argv[1], sys.argv[2], (sys.argv[3] if sys.argv[3] != "-" else None), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
w = L.Writer(d, mode, crash_at, crash_after)
w.recover()
for i in range(start, start + n): w.submit(f"r{i:06d}", {"n": i, "payload": "x" * 40})
w.drain()
