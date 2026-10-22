import tempfile
import unittest
from pathlib import Path
from mini_agent.durable_execution import DurableStore, Conflict, resume_decision

class DurableTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.path=Path(self.temp.name)/"state.db"
  self.s=DurableStore(self.path)
  self.s.create("run")
 def tearDown(self):
  self.s.close()
  self.temp.cleanup()
 def test_restart_and_atomic_projection(self):
  s=self.s.append("run","e1","started",{},expected_version=0,cursor=0,status="running")
  self.assertEqual(s.version,1)
  self.s.close()
  self.s=DurableStore(self.path)
  self.assertEqual(self.s.load("run").status,"running")
  self.assertEqual(len(self.s.history("run")),1)
 def test_duplicate_event_and_version_conflict(self):
  self.s.append("run","e1","started",{},expected_version=0,cursor=0,status="running")
  self.assertEqual(self.s.append("run","e1","started",{},expected_version=0,cursor=0,status="running").version,1)
  with self.assertRaises(Conflict):
   self.s.append("run","e2","step",{},expected_version=0,cursor=1,status="running")
  with self.assertRaises(Conflict):
   self.s.append("run","e1","different",{},expected_version=1,cursor=1,status="running")
  self.assertEqual(len(self.s.history("run")),1)
 def test_unknown_side_effect_must_reconcile(self):
  self.assertEqual(self.s.begin_operation("run","send"),"pending")
  self.s.close()
  self.s=DurableStore(self.path)
  self.assertEqual(resume_decision(self.s,"run","send"),"reconcile_external_effect_before_retry")
  self.s.confirm_operation("run","send","message-id-1")
  self.assertEqual(resume_decision(self.s,"run","send"),"use_recorded_result")
  with self.assertRaises(Conflict):
   self.s.confirm_operation("run","send","message-id-2")
 def test_terminal_and_cursor(self):
  self.s.append("run","e1","done",{},expected_version=0,cursor=1,status="completed")
  with self.assertRaises(Conflict):
   self.s.append("run","e2","again",{},expected_version=1,cursor=2,status="running")

if __name__=="__main__":
 unittest.main()
