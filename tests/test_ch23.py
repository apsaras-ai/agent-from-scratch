import unittest
from decimal import Decimal
from mini_agent.cost_optimization import (
    Budget, CostOptimizer, Operation, OptimizationError, RateCard,
    ScopedFixtureCache, TokenUsage, cache_key, critical_path
)

RATE=RateCard(Decimal("2"),Decimal("8"),Decimal("0.5"))
BUDGET=Budget(Decimal("0.03"),500,3)

def plan(cached=0):
    return (
        Operation("retrieve","tool",100,tool_cost_usd=Decimal("0.001")),
        Operation("analyze","model",200,("retrieve",),
                  TokenUsage(3000,500,cached),RATE),
        Operation("check","verify",50,("analyze",)),
    )

class OptimizationTests(unittest.TestCase):
    def test_price_with_cached_tokens(self):
        usage=TokenUsage(3000,500,2000)
        self.assertEqual(RATE.price(usage),Decimal("0.007"))
    def test_price_without_cache(self):
        self.assertEqual(RATE.price(TokenUsage(3000,500)),Decimal("0.010"))
    def test_critical_path_and_parallel(self):
        ops=(Operation("a","tool",100),
             Operation("b","tool",110),
             Operation("c","verify",40,("a","b")))
        self.assertEqual(critical_path(ops),(150,250))
    def test_cycle_and_missing(self):
        with self.assertRaises(OptimizationError):
            critical_path((Operation("a","tool",2,("b",)),Operation("b","tool",3,("a",))))
        with self.assertRaises(OptimizationError):
            critical_path((Operation("a","tool",2,("missing",)),))
    def test_duplicate_id_rejected(self):
        with self.assertRaises(OptimizationError):
            critical_path((Operation("x","tool",1),Operation("x","tool",2)))
    def test_verifier_controls_acceptance(self):
        optimizer=CostOptimizer(lambda x:(x=="safe","independent_fixture"))
        bad=optimizer.evaluate("unsafe",plan(),BUDGET)
        self.assertFalse(bad.accepted)
        self.assertTrue(bad.budget_ok)
        self.assertFalse(bad.verified)
    def test_overbudget_not_accepted(self):
        opt=CostOptimizer(lambda x:(True,"verified"))
        r=opt.evaluate("safe",plan(),Budget(Decimal("0.001"),1000,3))
        self.assertFalse(r.accepted)
    def test_best_verified_cheapest(self):
        opt=CostOptimizer(lambda x:(x!="cheap-but-wrong","host_fixture"))
        best,all_results=opt.choose({
            "baseline":plan(),
            "cached":plan(cached=2000),
            "cheap-but-wrong":(Operation("x","tool",1),),
        },BUDGET)
        self.assertEqual(best.candidate,"cached")
        self.assertEqual(len(all_results),3)
    def test_no_verified_plan(self):
        with self.assertRaises(OptimizationError):
            CostOptimizer(lambda x:(False,"bad")).choose({"x":plan()},BUDGET)
    def test_cache_scoping(self):
        common=dict(model="fixture",prompt="hello",policy_version="v1",tool_version="v1")
        a=cache_key(tenant="tenant-a",**common)
        b=cache_key(tenant="tenant-b",**common)
        changed=cache_key(tenant="tenant-a",**{**common,"policy_version":"v2"})
        self.assertNotEqual(a,b)
        self.assertNotEqual(a,changed)
        cache=ScopedFixtureCache()
        cache.put(a,"answer")
        self.assertEqual(cache.get(a),"answer")
        self.assertIsNone(cache.get(b))
    def test_usage_validation(self):
        for u in [(-1,1,0),(1,1,2),(True,1,0)]:
            with self.assertRaises(OptimizationError):
                TokenUsage(*u)
    def test_rate_validation(self):
        for value in ["NaN","Infinity","-1"]:
            with self.assertRaises(OptimizationError):
                RateCard(value,"1","1")
    def test_operation_requires_rate_for_usage(self):
        with self.assertRaises(OptimizationError):
            Operation("x","model",100,usage=TokenUsage(1,1))
        with self.assertRaises(OptimizationError):
            Operation("x","tool",100,usage=TokenUsage(1,1),rate=RATE)
    def test_budget_validation(self):
        with self.assertRaises(OptimizationError):
            Budget(Decimal("1"),float("nan"),1)
        with self.assertRaises(OptimizationError):
            Budget(Decimal("1"),100,True)
    def test_invalid_verifier(self):
        with self.assertRaises(OptimizationError):
            CostOptimizer(lambda _:("yes","fake")).evaluate("x",plan(),BUDGET)

if __name__=="__main__":
    unittest.main()
