"""Chapter 12: SQLite restart, checkpoint and ambiguous external-effect recovery."""
import tempfile
from pathlib import Path
from mini_agent.durable_execution import DurableStore, resume_decision

def main():
 with tempfile.TemporaryDirectory() as folder:
  db=Path(folder)/"runs.db"
  store=DurableStore(db)
  store.create("run-demo")
  store.append("run-demo","evt-1","run.started",{},expected_version=0,cursor=0,status="running")
  store.begin_operation("run-demo","tool-write-1")
  store.close()
  recovered=DurableStore(db)
  print("Recovered:",recovered.load("run-demo"))
  print("Pending external effect:",resume_decision(recovered,"run-demo","tool-write-1"))
  recovered.confirm_operation("run-demo","tool-write-1","verified-result")
  print("After reconciliation:",resume_decision(recovered,"run-demo","tool-write-1"))
  recovered.close()

if __name__=="__main__":
 main()
