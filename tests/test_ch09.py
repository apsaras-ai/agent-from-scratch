"""Chapter 9 memory permission, expiry, updates and retrieval tests."""
import unittest
from datetime import datetime, timedelta, timezone
from mini_agent.memory_service import (
 Access, MemoryRecord, MemoryService, MemoryErrorCode,
 lexical_score, hashed_vector, cosine, grounded_context,
)

NOW=datetime(2026,10,21,tzinfo=timezone.utc)
def rec(mid, owner="alice", text="CI lock conflict FIX-42", expiry=None, observed=None):
    return MemoryRecord(mid,"t",owner,"episodic","test result",text,
                        "ci://run",observed or NOW-timedelta(days=1),expiry)

class MemoryTests(unittest.TestCase):
 def setUp(self):
    self.s=MemoryService()
    self.a=Access("t","alice")
    self.b=Access("t","bob")
    self.s.write(rec("one"),self.a)

 def test_scoped_before_ranking(self):
    self.s.write(rec("private","bob","CI lock conflict secret"),self.b)
    for mode in ("lexical","vector","hybrid"):
      self.assertEqual([h.record.memory_id for h in self.s.retrieve(
          "CI lock conflict",actor=self.a,now=NOW,mode=mode)],["one"])
    with self.assertRaises(KeyError): self.s.get("private",self.a)
    with self.assertRaises(PermissionError): self.s.write(rec("new","bob"),self.a)

 def test_version_revision_and_retract(self):
    new=self.s.revise("one",new_id="two",content="fixed lock conflict",
        source="ci://verified",observed_at=NOW,actor=self.a)
    self.assertEqual(new.version,2)
    self.assertEqual(self.s.get("one",self.a).status,"superseded")
    self.assertEqual([x.record.memory_id for x in self.s.retrieve(
        "lock",actor=self.a,now=NOW)],["two"])
    self.s.retract("two",self.a)
    self.assertEqual(self.s.retrieve("lock",actor=self.a,now=NOW),[])

 def test_expiry_future_and_input_validation(self):
    self.s.write(rec("old",expiry=NOW-timedelta(seconds=1)),self.a)
    self.s.write(rec("future",observed=NOW+timedelta(days=1)),self.a)
    self.assertEqual([x.record.memory_id for x in self.s.retrieve(
        "lock",actor=self.a,now=NOW)],["one"])
    with self.assertRaises(MemoryErrorCode):
      rec("bad",observed=datetime(2026,10,21))
    with self.assertRaises(MemoryErrorCode): self.s.write(rec("one"),self.a)
    with self.assertRaises(ValueError):
      self.s.retrieve("lock",actor=self.a,now=NOW,top_k=0)

 def test_rank_and_provenance(self):
    self.assertAlmostEqual(lexical_score("lock error","lock detected"),0.5)
    a=hashed_vector("hello world")
    self.assertAlmostEqual(cosine(a,a),1)
    h=self.s.retrieve("lock",actor=self.a,now=NOW)
    evidence=grounded_context(h)
    self.assertEqual(evidence[0]["source"],"ci://run")
    self.assertIn("untrusted",evidence[0]["notice"].lower())

if __name__=="__main__":
 unittest.main()
