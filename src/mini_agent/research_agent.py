"""Chapter 13: offline, source-grounded research agent.

Search is backed by a deterministic fixture corpus, NOT the live internet.
Claims must point to explicit evidence IDs. Conflict detection surfaces
disagreement instead of selecting a convenient fact. No LLM is required.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from urllib.parse import urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.netloc:
        raise ValueError("Only absolute HTTP(S) evidence URLs are allowed")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))


@dataclass(frozen=True)
class Source:
    source_id: str
    title: str
    url: str
    publisher: str
    text: str

    def __post_init__(self):
        if not all((self.source_id, self.title, self.publisher, self.text)):
            raise ValueError("Source fields cannot be empty")
        canonical_url(self.url)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_id: str
    subject: str
    predicate: str
    value: str
    quote: str


@dataclass(frozen=True)
class Claim:
    subject: str
    predicate: str
    value: str
    evidence_ids: tuple[str, ...]


class FixtureSearch:
    def __init__(self, sources: list[Source], evidence: list[Evidence]):
        self.sources = {s.source_id: s for s in sources}
        self.evidence = {e.evidence_id: e for e in evidence}
        if len(self.sources) != len(sources) or len(self.evidence) != len(evidence):
            raise ValueError("Duplicate source/evidence ID")
        if any(e.source_id not in self.sources for e in evidence):
            raise ValueError("Evidence source not found")
        if any(e.quote not in self.sources[e.source_id].text for e in evidence):
            raise ValueError("Evidence quote must appear verbatim in source")

    def search(self, query: str, *, limit: int = 10) -> list[Source]:
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = set(re.findall(r"[a-z0-9_]+", query.lower()))
        ranked = []
        for source in self.sources.values():
            haystack = (source.title + " " + source.text).lower()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, source))
        ranked.sort(key=lambda row: (-row[0], row[1].source_id))
        seen, result = set(), []
        for _, source in ranked:
            url = canonical_url(source.url)
            if url in seen:
                continue
            seen.add(url)
            result.append(source)
            if len(result) == limit:
                break
        return result


def verify_claim(claim: Claim, store: FixtureSearch) -> bool:
    if not claim.evidence_ids:
        return False
    for evidence_id in claim.evidence_ids:
        evidence = store.evidence.get(evidence_id)
        if evidence is None or (evidence.subject, evidence.predicate, evidence.value) != (
            claim.subject, claim.predicate, claim.value
        ):
            return False
    return True


def conflicts(claims: list[Claim]) -> list[tuple[Claim, Claim]]:
    pairs = []
    for i, left in enumerate(claims):
        for right in claims[i + 1:]:
            if (left.subject, left.predicate) == (right.subject, right.predicate) and left.value != right.value:
                pairs.append((left, right))
    return pairs


def report(question: str, claims: list[Claim], store: FixtureSearch) -> str:
    supported = [c for c in claims if verify_claim(c, store)]
    unresolved = conflicts(supported)
    lines = ["# Research brief", "", "Question: " + question, "",
             "## Evidence-backed statements"]
    for claim in supported:
        ids = ", ".join("[" + item + "]" for item in claim.evidence_ids)
        lines.append(f"- {claim.subject} / {claim.predicate}: {claim.value} {ids}")
    lines.extend(["", "## Conflicting evidence"])
    for left, right in unresolved:
        lines.append(f"- {left.subject}/{left.predicate}: {left.value} vs {right.value} (requires follow-up)")
    lines.extend(["", "## Sources"])
    source_ids = sorted({store.evidence[eid].source_id for c in supported for eid in c.evidence_ids})
    for sid in source_ids:
        s = store.sources[sid]
        lines.append(f"- [{sid}] {s.title} — {s.publisher}: {s.url}")
    if len(supported) < len(claims):
        lines.append(f"\nExcluded {len(claims)-len(supported)} unverified claims.")
    return "\n".join(lines)
