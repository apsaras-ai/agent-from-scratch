"""Chapter 23: synthetic pricing and latency optimization; no API usage."""
from decimal import Decimal
from mini_agent.cost_optimization import (
    Budget,CostOptimizer,Operation,RateCard,TokenUsage
)

rate=RateCard(Decimal("2"),Decimal("8"),Decimal("0.5"))
def plan(cached=False):
    return (
        Operation("search","tool",100,tool_cost_usd=Decimal(".001")),
        Operation("model","model",200,("search",),
                  TokenUsage(3000,500,2000 if cached else 0),rate),
        Operation("verify","verify",50,("model",)),
    )

def main():
    optimizer=CostOptimizer(lambda candidate:(candidate!="wrong","host_fixture"))
    best,results=optimizer.choose(
        {"baseline":plan(),"cache-aware":plan(True),
         "wrong":(Operation("shortcut","tool",1),)},
        Budget(Decimal(".03"),500,3))
    for x in results:
        print(x.candidate,"cost_usd=",x.total_cost_usd,
              "critical_path_ms=",x.critical_path_ms,
              "verified=",x.verified,"accepted=",x.accepted)
    print("Selected:",best.candidate)
    assert best.candidate=="cache-aware"
    print("Synthetic token prices/durations; no real LLM benchmark.")

if __name__=="__main__":
    main()
