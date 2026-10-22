"""Offline fixture-backed Research Agent demonstration."""
from mini_agent.research_agent import Source, Evidence, Claim, FixtureSearch, report

def fixture():
    sources=[
        Source("s1","Runtime Release Note","https://example.org/release?tracking=1","Maintainer",
               "Project runtime release 2.0 supports Python 3.11."),
        Source("s2","Legacy README","https://example.org/legacy","Community",
               "Project runtime release 2.0 supports Python 3.10."),
        Source("s3","Duplicate Release Link","https://example.org/release?other=2","Mirror",
               "Project runtime release 2.0 supports Python 3.11."),
    ]
    evidence=[
        Evidence("e1","s1","runtime 2.0","minimum Python","3.11",
                 "Project runtime release 2.0 supports Python 3.11."),
        Evidence("e2","s2","runtime 2.0","minimum Python","3.10",
                 "Project runtime release 2.0 supports Python 3.10."),
    ]
    return FixtureSearch(sources,evidence)

def main():
    store=fixture()
    print("Found sources:",[s.source_id for s in store.search("runtime release Python")])
    claims=[
        Claim("runtime 2.0","minimum Python","3.11",("e1",)),
        Claim("runtime 2.0","minimum Python","3.10",("e2",)),
        Claim("runtime 2.0","minimum Python","3.12",("missing",)),
    ]
    print(report("Which Python version does runtime 2.0 require?",claims,store))
    print("Synthetic evidence only; not a real research result.")

if __name__=="__main__":
    main()
