"""Chapter 9 deterministic memory search and revision."""
from datetime import datetime, timedelta, timezone
from mini_agent.memory_service import Access, MemoryRecord, MemoryService, grounded_context

def main():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    s = MemoryService()
    alice, bob = Access("team", "alice"), Access("team", "bob")
    data = [
        MemoryRecord("m1", "team", "alice", "episodic", "test failure",
                     "CI test reported lock conflict FIX-42 in worker log",
                     "ci://run-1/test", now-timedelta(days=2)),
        MemoryRecord("m2", "team", "alice", "semantic", "build policy",
                     "Our build system requires Python 3.11",
                     "docs://build", now-timedelta(days=20)),
        MemoryRecord("m3", "team", "bob", "semantic", "private",
                     "Private lock conflict FIX-999",
                     "docs://other", now-timedelta(days=1)),
        MemoryRecord("m4", "team", "alice", "episodic", "expired",
                     "old lock conflict FIX-123",
                     "ci://old", now-timedelta(days=8),
                     expires_at=now-timedelta(days=1)),
    ]
    for r in data:
        s.write(r, alice if r.owner_id == "alice" else bob)
    for mode in ("lexical", "vector", "hybrid"):
        print(mode, [h.record.memory_id for h in s.retrieve(
            "CI lock conflict", actor=alice, now=now, mode=mode)])
    s.revise("m1", new_id="m1-v2", content="Verified CI lock conflict FIX-42",
             source="ci://run-2/verification", observed_at=now, actor=alice)
    print("after revision", [h.record.memory_id for h in s.retrieve(
        "CI lock conflict", actor=alice, now=now)])
    print("grounded", grounded_context(
        s.retrieve("CI lock conflict", actor=alice, now=now, top_k=1)))
    print("Synthetic data; hashed character vectors are NOT neural embeddings")

if __name__ == "__main__":
    main()
