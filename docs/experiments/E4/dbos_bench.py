"""E4 throughput: DBOS workflows per second with three trivial steps on SQLite, and the same three steps as plain SQLite outbox transactions."""
import os, sqlite3, sys, tempfile, time
from dbos import DBOS
d = tempfile.mkdtemp(prefix="e4b_")
DBOS(config={"name": "e4-bench", "system_database_url": f"sqlite:///{d}/dbos.sqlite", "run_admin_server": False, "log_level": "ERROR"})
@DBOS.step()
def s1(i): return i
@DBOS.step()
def s2(i): return i
@DBOS.step()
def s3(i): return i
@DBOS.workflow()
def wf(i):
    s1(i); s2(i); s3(i)
DBOS.launch()
t = time.time(); n = 0
while time.time() - t < 6:
    wf(n); n += 1
dbos_rate = n / (time.time() - t)
c = sqlite3.connect(f"{d}/plain.db", isolation_level=None)
c.execute("pragma journal_mode=wal"); c.execute("pragma synchronous=FULL")
c.execute("create table outbox(id integer primary key, done int)"); c.execute("create table ledger(seq integer primary key, outbox_id int unique)")
t = time.time(); m = 0
while time.time() - t < 6:
    c.execute("begin immediate"); c.execute("insert into outbox(id,done) values(?,0)", (m,)); c.execute("commit")
    c.execute("begin immediate"); c.execute("insert into ledger(outbox_id) values(?)", (m,)); c.execute("update outbox set done=1 where id=?", (m,)); c.execute("commit")
    m += 1
print({"dbos_workflows_per_s": round(dbos_rate, 1), "plain_two_transaction_outbox_per_s": round(m / (time.time() - t), 1), "dbos_n": n, "plain_n": m})
