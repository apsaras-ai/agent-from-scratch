"""Research Agent citation, conflict and source integrity tests."""
import unittest
from mini_agent.research_agent import (
 Source, Evidence, Claim, FixtureSearch, verify_claim, conflicts, report,
 canonical_url,
)

class ResearchTests(unittest.TestCase):
 def setUp(self):
  self.s=FixtureSearch([
   Source("a","Release","https://example.com/item?q=1","Team","The runtime supports Python 3.11."),
   Source("b","Old docs","https://example.com/old","Legacy","The runtime supports Python 3.10."),
   Source("c","Mirror","https://example.com/item?q=2","Mirror","The runtime supports Python 3.11."),
  ],[
   Evidence("ea","a","runtime","Python","3.11","The runtime supports Python 3.11."),
   Evidence("eb","b","runtime","Python","3.10","The runtime supports Python 3.10."),
  ])
 def test_url_dedup(self):
  self.assertEqual(len(self.s.search("runtime Python")),2)
  self.assertEqual(canonical_url("https://EXAMPLE.com/item?tracking=x"),"https://example.com/item")
 def test_evidence_binding(self):
  good=Claim("runtime","Python","3.11",("ea",))
  self.assertTrue(verify_claim(good,self.s))
  for c in (Claim("runtime","Python","3.12",("ea",)),
            Claim("runtime","Python","3.11",("unknown",)),
            Claim("runtime","Python","3.11",())):
   self.assertFalse(verify_claim(c,self.s))
 def test_conflicting_sources_remain_visible(self):
  claims=[Claim("runtime","Python","3.11",("ea",)),
          Claim("runtime","Python","3.10",("eb",))]
  self.assertEqual(len(conflicts(claims)),1)
  text=report("Which version?", claims,self.s)
  self.assertIn("Conflicting evidence",text)
  self.assertIn("[ea]",text)
  self.assertIn("[eb]",text)
 def test_wrong_quote_rejected(self):
  with self.assertRaises(ValueError):
   FixtureSearch([Source("a","x","https://example.com/x","x","Text")],
                 [Evidence("e","a","s","p","v","not contained")])
 def test_unverified_claim_excluded(self):
  text=report("Q",[Claim("x","y","z",("fake",))],self.s)
  self.assertIn("Excluded 1 unverified",text)
  self.assertNotIn("x / y: z",text)

if __name__=="__main__":
 unittest.main()
