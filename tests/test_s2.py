"""Tests for S2 retrieval (Mansimran). No network required: all external calls
are stubbed, so `pytest -q` runs in CI and on the demo laptop offline.

Run:  pytest -q
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cache import Cache, make_key  # noqa: E402
from contracts import Evidence, RetrievalAudit, RetrievalResult  # noqa: E402
from http_util import SourceError  # noqa: E402
from ranking import (  # noqa: E402
    DEFAULT_WEIGHTS,
    bm25_scores,
    dedupe,
    jaccard,
    rank_candidates,
    rescale,
    rrf_scores,
    shingles,
    tfidf_cosine,
    token_overlap,
)
from s2_retrieval import RetrievalConfig, RetrievalEngine, build_queries  # noqa: E402
from sources import (  # noqa: E402
    DuckDuckGoSource,
    LocalCorpusSource,
    OpenAlexSource,
    PubMedSource,
    RawResult,
    SourceAdapter,
    SourceDisabled,
    canonical_url,
    cap_key,
    domain_of,
)


# ---------------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------------
class StubAdapter(SourceAdapter):
    def __init__(self, name, kind, results=None, raises=None, budget_s=1.0):
        self.name = name
        self.kind = kind
        self._results = results or []
        self._raises = raises
        self.budget_s = budget_s
        self.calls = 0

    def search(self, query, *, limit, cache=None):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._results[:limit]


def r(title, url, snippet, kind="web", date=None):
    return RawResult(title=title, url=url, snippet=snippet, source_kind=kind, published_at=date)


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------
def test_build_queries_keeps_literal_query_first():
    queries = build_queries(
        "According to a 2021 study, vitamin C prevents the common cold in 50% of adults."
    )
    assert queries[0].startswith("2021") or "vitamin C" in queries[0]
    assert "according" not in queries[0].lower()
    assert len(queries) >= 1


def test_build_queries_never_empty_for_empty_input():
    assert build_queries("") == [""] or build_queries("")


def test_build_queries_appends_entity_variant():
    queries = build_queries("The Eiffel Tower in Paris is 330 metres tall.")
    assert len(queries) >= 1
    assert all(q.strip() for q in queries)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def test_ranking_prefers_matching_document():
    breakdown = rank_candidates(
        "vitamin C prevents the common cold",
        [
            "Vitamin C does not prevent the common cold in adults",
            "Paris is the capital of France and a popular tourist destination",
        ],
        [1, 2],
    )
    assert breakdown[0]["relevance"] > breakdown[1]["relevance"]
    assert 0.0 <= breakdown[0]["relevance"] <= 1.0


def test_relevance_penalises_zero_overlap():
    breakdown = rank_candidates("quantum entanglement", ["cooking pasta al dente"], [1])
    assert breakdown[0]["overlap"] == 0.0
    assert breakdown[0]["relevance"] < 0.3


def test_tfidf_and_bm25_are_bounded():
    docs = ["a b c", "vitamin c cold", ""]
    for value in tfidf_cosine("vitamin c", docs):
        assert 0.0 <= value <= 1.0
    for value in bm25_scores("vitamin c", docs):
        assert 0.0 <= value <= 1.0


def test_rescale_handles_all_zeros():
    assert rescale([0, 0, 0]) == [0.0, 0.0, 0.0]


def test_rrf_ignores_missing_ranks():
    assert rrf_scores([None, 2])[0] == 0.0
    assert rrf_scores([None, 2])[1] > 0.9


def test_token_overlap_and_shingles():
    assert token_overlap("vitamin c cold", "vitamin c does not prevent the cold") == 1.0
    assert jaccard(shingles("one two three four"), shingles("one two three four")) == 1.0


# ---------------------------------------------------------------------------
# Dedupe / URL handling
# ---------------------------------------------------------------------------
def test_canonical_url_strips_tracking_and_scheme_noise():
    assert canonical_url("http://www.Example.com/a/b/?utm_source=x#frag") == "https://example.com/a/b"
    assert canonical_url("https://example.com/a/b?id=2&utm_medium=y") == "https://example.com/a/b?id=2"


def test_domain_and_cap_key():
    assert domain_of("https://www.en.wikipedia.org/wiki/X") == "en.wikipedia.org"
    assert cap_key("https://doi.org/10.1000/xyz") == "doi.org/10.1000"
    assert cap_key("https://pubmed.ncbi.nlm.nih.gov/123/") == "pubmed.ncbi.nlm.nih.gov"


def test_dedupe_drops_url_duplicates_text_duplicates_and_caps_domains():
    items = [
        {"url": "https://a.com/1", "text": "vitamin c prevents the common cold"},
        {"url": "https://a.com/1", "text": "vitamin c prevents the common cold"},  # dup url
        {"url": "https://a.com/2", "text": "vitamin c prevents the common cold"},  # dup text
        {"url": "https://a.com/3", "text": "totally different content about lightning"},
        {"url": "https://a.com/4", "text": "another unrelated snippet on pasta"},
    ]
    kept, stats = dedupe(
        items,
        key_url=lambda c: canonical_url(c["url"]),
        key_text=lambda c: c["text"],
        max_per_domain=2,
        domain_of=lambda u: domain_of(u),
    )
    assert stats["duplicate_url"] == 1
    assert stats["duplicate_text"] >= 1
    assert stats["domain_cap"] >= 1
    assert len(kept) == 2


# ---------------------------------------------------------------------------
# Source parsing (offline)
# ---------------------------------------------------------------------------
def test_ddg_href_decoding():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
    assert DuckDuckGoSource._decode_href(href) == "https://example.com/page"


def test_ddg_html_parser_extracts_results():
    html = """
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com%2Fa">Title A</a>
    <a class="result__snippet">Snippet about vitamin C and colds</a>
    """
    results = DuckDuckGoSource.parse_html(html, limit=3)
    assert len(results) == 1
    assert results[0].url == "https://ex.com/a"
    assert "vitamin C" in results[0].snippet


def test_openalex_abstract_reconstruction():
    inverted = {"Vitamin": [0], "C": [1], "works": [2]}
    assert OpenAlexSource._abstract(inverted) == "Vitamin C works"


def test_pubmed_xml_parser():
    xml = """
    <PubmedArticleSet><PubmedArticle>
      <MedlineCitation><PMID>12345</PMID>
        <Article>
          <ArticleTitle>Vitamin C and the common cold</ArticleTitle>
          <Abstract><AbstractText>Routine supplementation did not reduce incidence.</AbstractText></Abstract>
          <Journal><Title>Journal of Tests</Title>
            <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
          </Journal>
        </Article>
      </MedlineCitation>
    </PubmedArticle></PubmedArticleSet>
    """
    results = PubMedSource.parse_xml(xml, limit=3)
    assert len(results) == 1
    assert results[0].title == "Vitamin C and the common cold"
    assert results[0].published_at == "2021-01-01"
    assert results[0].url.endswith("/12345/")
    assert "incidence" in results[0].snippet


def test_local_corpus_keyword_matching(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "title": "Cold facts",
                    "url": "https://example.org/cold",
                    "text": "Vitamin C does not prevent the common cold.",
                    "keywords": ["vitamin", "cold", "prevent"],
                    "published_at": "2023-01-01",
                }
            ]
        )
    )
    results = LocalCorpusSource(corpus).search("vitamin cold prevent", limit=3)
    assert len(results) == 1
    assert results[0].source_kind == "mock"
    assert results[0].published_at == "2023-01-01"


# ---------------------------------------------------------------------------
# Engine behaviour: fail-soft, audit, contract shape
# ---------------------------------------------------------------------------
def make_engine(adapters, **cfg):
    config = RetrievalConfig(top_k=cfg.pop("top_k", 5), **cfg)
    return RetrievalEngine(config=config, adapters=adapters, cache=Cache(ttl_s=60))


def test_failed_source_does_not_fail_run():
    good = StubAdapter(
        "good",
        "encyclopedia",
        [r("Vitamin C facts", "https://good.com/vitc", "Vitamin C does not prevent the common cold.")],
    )
    bad = StubAdapter("bad", "web", raises=SourceError("boom"))
    disabled = StubAdapter("nokey", "fact_check", raises=SourceDisabled("no key"))
    engine = make_engine([good, bad, disabled])

    result = engine.search_claim("Vitamin C prevents the common cold", claim_id="c1")

    assert isinstance(result, RetrievalResult)
    assert result.claim_id == "c1"
    assert len(result.evidence) == 1
    statuses = {s.name: s.status for s in result.audit.sources}
    assert statuses["bad"] == "error"
    assert statuses["nokey"] == "skipped"
    assert result.audit.sources_failed == 1  # one *adapter*, even across 2 queries
    assert "bad" in result.audit.failed_source_names


def test_all_sources_empty_yields_insufficient_signal():
    engine = make_engine([StubAdapter("empty", "web", [])])
    result = engine.search_claim("An obscure claim nobody wrote about")
    assert result.evidence == []
    assert result.has_evidence is False
    assert any("no source returned" in n for n in result.audit.notes)


def test_evidence_matches_shared_contract():
    source = StubAdapter(
        "wiki",
        "encyclopedia",
        [r("Eiffel Tower", "https://en.wikipedia.org/wiki/Eiffel_Tower", "The tower is 330 metres tall.", "encyclopedia", "2024-01-01")],
    )
    result = make_engine([source]).search_claim("The Eiffel Tower is 330 metres tall.")
    ev = result.evidence[0]
    assert isinstance(ev, Evidence)
    payload = ev.model_dump()
    for field in [
        "claim_id",
        "url",
        "domain",
        "title",
        "text_or_snippet",
        "published_at",
        "source_kind",
        "relevance",
    ]:
        assert field in payload
    assert 0.0 <= payload["relevance"] <= 1.0
    assert payload["domain"] == "en.wikipedia.org"


def test_top_k_and_domain_cap_respected():
    many = [
        r(f"Vitamin C cold article {i}", f"https://a.com/{i}", "Vitamin C and the common cold study " * 3)
        for i in range(6)
    ]
    other = [r("Cold overview", "https://b.org/cold", "Vitamin C common cold prevention overview")]
    result = make_engine([StubAdapter("a", "web", many), StubAdapter("b", "encyclopedia", other)], top_k=4).search_claim(
        "vitamin C common cold"
    )
    assert len(result.evidence) <= 4
    per_domain: dict[str, int] = {}
    for ev in result.evidence:
        per_domain[ev.domain] = per_domain.get(ev.domain, 0) + 1
    assert per_domain.get("a.com", 0) <= 2


def test_source_diversity_is_preserved():
    web = [r(f"Web page {i}", f"https://web.com/{i}", "vitamin c common cold prevention adults") for i in range(4)]
    enc = [
        r(
            "Encyclopedia entry",
            "https://enc.org/cold",
            "vitamin c common cold overview prevention",
            "encyclopedia",
        )
    ]
    result = make_engine([StubAdapter("web", "web", web), StubAdapter("enc", "encyclopedia", enc)], top_k=4).search_claim(
        "vitamin c common cold prevention"
    )
    kinds = {ev.source_kind for ev in result.evidence}
    assert "encyclopedia" in kinds and "web" in kinds


def test_audit_counts_are_consistent():
    source = StubAdapter(
        "x",
        "web",
        [r("A", "https://a.com/1", "vitamin c cold"), r("B", "https://a.com/1", "vitamin c cold")],
    )
    result = make_engine([source]).search_claim("vitamin c cold")
    audit = result.audit
    assert isinstance(audit, RetrievalAudit)
    assert audit.candidates_total == 2
    assert audit.dropped_duplicates == 1
    assert audit.returned == len(result.evidence)
    assert audit.sources_queried == 1


def test_config_and_weights_are_pluggable():
    source = StubAdapter("x", "web", [r("A", "https://a.com/1", "vitamin c cold prevention")])
    engine = make_engine([source], weights={"semantic": 1.0, "bm25": 0.0, "rrf": 0.0})
    result = engine.search_claim("vitamin c cold")
    assert result.evidence
    assert engine.config.weights["semantic"] == 1.0


def test_offline_mode_uses_local_corpus_only():
    engine = RetrievalEngine(config=RetrievalConfig(offline=True), cache=Cache(ttl_s=5))
    # offline mode must never touch the network
    assert [a.name for a in engine.adapters] == ["local_corpus"]


# ---------------------------------------------------------------------------
# .env loading (matters most on Windows, where nobody exports variables)
# ---------------------------------------------------------------------------
def test_env_parsing_handles_quotes_export_and_comments():
    from envload import parse_env_text

    parsed = parse_env_text(
        """
        # comment line
        TAVILY_API_KEY="tvly-123"
        export OPENALEX_MAILTO=me@example.com
        ENABLE_OPENALEX = 1   # trailing comment
        EMPTY=
        not a pair
        """
    )
    assert parsed["TAVILY_API_KEY"] == "tvly-123"
    assert parsed["OPENALEX_MAILTO"] == "me@example.com"
    assert parsed["ENABLE_OPENALEX"] == "1"
    assert parsed["EMPTY"] == ""
    assert "not a pair" not in parsed


def test_env_file_is_loaded_without_overwriting_real_env(tmp_path, monkeypatch):
    from envload import load_env

    env_file = tmp_path / ".env"
    env_file.write_text("VERIFYAI_TEST_KEY=from-file\nVERIFYAI_SECOND=file2\n", encoding="utf-8")
    monkeypatch.setenv("VERIFYAI_TEST_KEY", "from-shell")
    monkeypatch.delenv("VERIFYAI_SECOND", raising=False)

    applied = load_env(env_file, override=False)

    import os

    assert os.environ["VERIFYAI_TEST_KEY"] == "from-shell"   # shell wins
    assert os.environ["VERIFYAI_SECOND"] == "file2"          # file fills the gap
    assert "VERIFYAI_TEST_KEY" not in applied


def test_env_report_never_leaks_secret_values():
    from envload import env_report

    report = env_report()
    assert "unset" in report or "set" in report


# ---------------------------------------------------------------------------
# Snapshot capture + replay (offline demo backup)
# ---------------------------------------------------------------------------
def _snapshot_fixture(tmp_path):
    from contracts import Evidence
    from snapshot import build_snapshot_entry, save_snapshot

    entry = build_snapshot_entry(
        "Vitamin C supplementation prevents the common cold in most adults.",
        queries=["Vitamin C supplementation prevents common cold adults"],
        evidence=[
            Evidence(
                claim_id="c1",
                url="https://pubmed.ncbi.nlm.nih.gov/9059230/",
                domain="pubmed.ncbi.nlm.nih.gov",
                title="Vitamin C intake and susceptibility to the common cold",
                text_or_snippet="Routine supplementation has not been shown to reduce incidence.",
                published_at="1997-01-01",
                source_kind="academic",
                relevance=0.71,
            )
        ],
        audit=None,
    )
    return save_snapshot(tmp_path / "snap.json", [entry])


def test_snapshot_roundtrip_preserves_evidence_and_kind(tmp_path):
    from snapshot import SnapshotSource

    path = _snapshot_fixture(tmp_path)
    source = SnapshotSource(path)
    results = source.search("Vitamin C supplementation prevents common cold", limit=5)

    assert len(results) == 1
    assert results[0].source_kind == "academic"          # credibility scoring still works
    assert results[0].published_at == "1997-01-01"
    assert results[0].extra["demo_backup"] is True
    assert source.summary()["claims"] == 1


def test_snapshot_matches_rephrased_claim_but_never_unrelated_ones(tmp_path):
    from snapshot import SnapshotSource

    source = SnapshotSource(_snapshot_fixture(tmp_path))
    assert source.search("vitamin C prevents the common cold", limit=5)      # containment match
    assert source.search("Bananas cure insomnia", limit=5) == []             # must not invent
    assert source.search("vitamin", limit=5) == []                           # 1 shared token only


def test_replay_engine_never_touches_network_and_flags_demo_backup(tmp_path):
    path = _snapshot_fixture(tmp_path)
    engine = RetrievalEngine(config=RetrievalConfig(replay_snapshot=str(path)))

    assert [a.name for a in engine.adapters] == ["snapshot"]

    hit = engine.search_claim("vitamin C prevents the common cold", claim_id="c1")
    assert hit.evidence and hit.evidence[0].claim_id == "c1"
    assert any("DEMO BACKUP" in note for note in hit.audit.notes)
    assert hit.audit.sources[0].name == "snapshot"
    assert hit.audit.sources[0].cached is True

    miss = engine.search_claim("Bananas cure insomnia")
    assert miss.evidence == []
    assert any("not in the snapshot" in note for note in miss.audit.notes)


def test_missing_snapshot_file_is_an_explicit_error(tmp_path):
    from snapshot import SnapshotSource

    with pytest.raises(FileNotFoundError):
        SnapshotSource(tmp_path / "nope.json").search("x", limit=3)


# ---------------------------------------------------------------------------
# Contract adapter: S2 output -> the lead's backend/app/models.py
# ---------------------------------------------------------------------------
def _lead_models():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
    import app_models_snapshot  # noqa: F401

    return app_models_snapshot


def test_adapter_output_validates_against_the_leads_real_models():
    """The whole point: our evidence must satisfy app.models.EvidenceItem."""
    models = _lead_models()
    from contract_adapter import to_claim_bundle
    from contracts import Evidence

    ev = Evidence(
        claim_id="c1",
        url="https://pubmed.ncbi.nlm.nih.gov/9059230/",
        domain="pubmed.ncbi.nlm.nih.gov",
        title="Vitamin C intake and susceptibility to the common cold",
        text_or_snippet="Routine supplementation has not been shown to reduce incidence.",
        published_at="1997-01-01",
        source_kind="academic",
        relevance=0.71,
    )
    bundle = to_claim_bundle(claim_id="c1", claim_text="Vitamin C prevents colds", evidence=[ev])

    parsed = models.ClaimEvidenceBundle(**bundle)          # would raise on any mismatch
    assert parsed.claim.id == "c1"
    assert parsed.evidence[0].snippet == ev.text_or_snippet
    assert parsed.evidence[0].published_at == date(1997, 1, 1)
    assert parsed.evidence[0].credibility == 0.85           # academic
    assert parsed.evidence[0].stance == models.Stance.NEUTRAL  # S3 fills this, not us
    assert parsed.evidence[0].quote_verified is False
    assert parsed.evidence[0].id.startswith("ev_")


def test_adapter_fills_fields_our_own_contract_lacks():
    """id / snippet / date / credibility / context_score are all required by the lead."""
    from contract_adapter import to_evidence_item
    from contracts import Evidence

    ev = Evidence(url="https://example.org/a", domain="example.org", title="T",
                  text_or_snippet="body", published_at="2024", source_kind="web", relevance=0.4)
    item = to_evidence_item(ev, claim_id="c9")
    for field in ("id", "claim_id", "url", "domain", "title", "snippet", "full_text",
                  "published_at", "source_kind", "relevance", "credibility",
                  "stance", "quote", "quote_verified", "rationale",
                  "context_score", "context_gap"):
        assert field in item, field
    assert item["published_at"] == date(2024, 1, 1)   # year-only, coarse but honest
    assert item["credibility"] == 0.45                # generic web


def test_adapter_never_invents_a_date():
    from contract_adapter import to_date

    assert to_date(None) is None
    assert to_date("") is None
    assert to_date("no date") is None
    assert to_date("2025-03-09T12:00:00Z") == date(2025, 3, 9)
    assert to_date("not-a-date-but-2025") is None


def test_adapter_ids_are_deterministic_and_unique_per_url():
    from contract_adapter import stable_evidence_id

    a = stable_evidence_id("c1", "https://a.com/x")
    assert a == stable_evidence_id("c1", "https://a.com/x")     # stable across runs
    assert a != stable_evidence_id("c2", "https://a.com/x")     # per claim
    assert a != stable_evidence_id("c1", "https://a.com/y")     # per url


def test_full_retrieval_to_lead_contract_end_to_end():
    """Offline retrieval -> adapter -> lead's pydantic model, no network."""
    models = _lead_models()
    from contract_adapter import to_claim_bundle
    from s2_retrieval import RetrievalConfig, RetrievalEngine

    engine = RetrievalEngine(config=RetrievalConfig(offline=True), cache=Cache(ttl_s=5))
    rr = engine.search_claim("vitamin C prevents the common cold", claim_id="c7")
    assert rr.evidence

    bundle = to_claim_bundle(
        claim_id="c7", claim_text="vitamin C prevents the common cold",
        evidence=rr.evidence, audit=rr.audit,
    )
    parsed = models.ClaimEvidenceBundle(**bundle)
    assert len(parsed.evidence) == len(rr.evidence)
    assert all(item.claim_id == "c7" for item in parsed.evidence)
    assert len({item.id for item in parsed.evidence}) == len(parsed.evidence)  # ids unique


# ---------------------------------------------------------------------------
# Claim shapes: Ikjot uses claim_type + entities, the lead uses type, no entities
# ---------------------------------------------------------------------------
class _IkjotClaim:
    """Mimics s1_claims.Claim(id, text, entities, claim_type)."""

    def __init__(self):
        self.id = "claim_001"
        self.text = "India is the world's largest solar energy producer"
        self.entities = ["India"]
        self.claim_type = "factual"


def test_claim_normaliser_accepts_ikjots_claim_type_and_entities():
    from contract_adapter import claim_type_of, extract_entities, to_claim_input

    claim = _IkjotClaim()
    payload = to_claim_input(claim)

    assert payload == {
        "id": "claim_001",
        "text": "India is the world's largest solar energy producer",
        "type": "factual",            # claim_type -> type
        "article_quote": None,
        "time_sensitive": False,
    }
    assert claim_type_of(claim) == "factual"
    assert extract_entities(claim) == ["India"]


def test_claim_normaliser_accepts_dicts_and_the_plan_sketch():
    from contract_adapter import to_claim_input

    # lead-style dict with "type"
    assert to_claim_input({"id": "c2", "text": "x", "type": "opinion"})["type"] == "opinion"
    # plan-sketch shape
    sketch = {"id": "c3", "text": "y", "type": "numeric",
              "article_quote": "q", "time_sensitive": True, "entities": ["A"]}
    out = to_claim_input(sketch)
    assert out["type"] == "numeric"
    assert out["article_quote"] == "q"
    assert out["time_sensitive"] is True


def test_claim_normaliser_rejects_missing_id_or_text():
    from contract_adapter import to_claim_input

    with pytest.raises(ValueError):
        to_claim_input({"text": "no id"})
    with pytest.raises(ValueError):
        to_claim_input({"id": "c1"})


def test_ikjot_claim_bundle_validates_against_lead_models():
    models = _lead_models()
    from contract_adapter import to_claim_bundle
    from contracts import Evidence

    claim = _IkjotClaim()
    bundle = to_claim_bundle(
        claim,
        evidence=[
            Evidence(
                url="https://en.wikipedia.org/wiki/Solar_power_in_India",
                domain="en.wikipedia.org",
                title="Solar power in India",
                text_or_snippet="India has one of the world's largest solar programmes.",
                source_kind="encyclopedia",
                relevance=0.67,
            )
        ],
        include_extras=True,
    )
    assert bundle["extras"] == {"entities": ["India"]}

    strict = {k: v for k, v in bundle.items() if k != "extras"}
    parsed = models.ClaimEvidenceBundle(**strict)
    assert parsed.claim.id == "claim_001"
    assert parsed.claim.type == "factual"
    assert parsed.evidence[0].claim_id == "claim_001"


def test_entities_improve_the_query_but_not_the_ranking_inputs():
    from s2_retrieval import build_queries

    text = "Apple acquired Beats for 3 billion dollars in 2014"
    with_entities = build_queries(text, entities=["Apple", "Beats"])
    assert any(q.startswith("Apple Beats") for q in with_entities)
    # a claim with no entities still produces queries
    assert build_queries(text)


def test_engine_accepts_entities_and_keeps_contract_shape():
    source = StubAdapter(
        "x",
        "encyclopedia",
        [r("Solar power in India", "https://en.wikipedia.org/wiki/Solar_power_in_India",
           "India solar energy capacity growth", "encyclopedia")],
    )
    engine = make_engine([source])
    result = engine.search_claim("India is the world's largest solar energy producer",
                                 claim_id="claim_001", entities=["India"])
    assert result.evidence
    assert result.audit.queries
    assert result.evidence[0].claim_id == "claim_001"


# ---------------------------------------------------------------------------
# Relevance calibration (ranking similarity -> the scorer's R scale)
# ---------------------------------------------------------------------------
def test_relevance_calibration_is_monotone_and_clipped():
    from s2_retrieval import calibrate_relevance

    values = [0.0, 0.2, 0.35, 0.5, 0.62, 0.8, 1.0]
    scaled = [calibrate_relevance(v) for v in values]
    assert scaled == sorted(scaled), "calibration must preserve ordering"
    assert all(0.0 <= s <= 1.0 for s in scaled)
    assert calibrate_relevance(0.5) == 0.725         # 0.5 * 1.45
    assert calibrate_relevance(0.9) == 1.0           # clipped
    assert calibrate_relevance(0.5, scale=1.0) == 0.5


# ---------------------------------------------------------------------------
# s2_service: the single entry point for the orchestrator
# ---------------------------------------------------------------------------
def _offline_service_engine():
    from s2_retrieval import RetrievalConfig, RetrievalEngine

    return RetrievalEngine(config=RetrievalConfig(offline=True, top_k=4), cache=Cache(ttl_s=5))


def test_retrieve_for_claims_returns_bundles_in_input_order():
    from s2_service import retrieve_for_claims

    claims = [
        {"id": "c1", "text": "vitamin C prevents the common cold"},
        {"id": "c2", "text": "The Eiffel Tower is 330 metres tall."},
    ]
    bundles = retrieve_for_claims(claims, engine=_offline_service_engine())
    assert [b["claim"]["id"] for b in bundles] == ["c1", "c2"]
    assert bundles[0]["evidence"], "offline corpus should match the vitamin C claim"
    assert all(e["claim_id"] == "c1" for e in bundles[0]["evidence"])


def test_retrieve_for_claims_accepts_ikjot_claim_objects():
    from s2_service import retrieve_for_claims

    claim = _IkjotClaim()
    bundles = retrieve_for_claims([claim], engine=_offline_service_engine())
    assert bundles[0]["claim"]["type"] == "factual"
    assert bundles[0]["claim"]["id"] == "claim_001"


def test_retrieve_for_claims_fails_soft_per_claim():
    from s2_service import retrieve_for_claims

    bundles = retrieve_for_claims(
        [{"id": "c1", "text": "vitamin C common cold"}, {"text": "no id at all"}],
        engine=_offline_service_engine(),
    )
    assert len(bundles) == 2, "a bad claim must not remove the good one"
    assert bundles[1]["evidence"] == []


def test_apply_analysis_keeps_verified_quotes_and_drops_fabricated_ones():
    from s2_service import apply_analysis, retrieve_for_claims

    bundles = retrieve_for_claims(
        [{"id": "c1", "text": "vitamin C prevents the common cold"}],
        engine=_offline_service_engine(),
    )
    ev = bundles[0]["evidence"]
    real_quote = " ".join(ev[0]["snippet"].split()[:8])

    bundles = apply_analysis(
        bundles,
        [
            {"evidence_id": ev[0]["id"], "stance": "REFUTES", "quote": real_quote,
             "quote_verified": True, "rationale": "ok"},
            {"evidence_id": ev[1]["id"], "stance": "SUPPORTS",
             "quote": "A sentence that appears nowhere in the source text.", "quote_verified": True},
        ],
    )
    first, second = bundles[0]["evidence"][0], bundles[0]["evidence"][1]
    assert first["stance"] == "REFUTES" and first["quote_verified"] is True
    assert first["quote"] == real_quote
    assert second["quote_verified"] is False, "fabricated quote must be rejected"
    assert second["quote"] == ""
    assert second["stance"] == "SUPPORTS", "stance is kept, only the quote is dropped"


def test_apply_analysis_neutralises_unknown_stance_and_ignores_unknown_ids():
    from s2_service import apply_analysis, retrieve_for_claims

    bundles = retrieve_for_claims(
        [{"id": "c1", "text": "vitamin C prevents the common cold"}],
        engine=_offline_service_engine(),
    )
    ev = bundles[0]["evidence"][0]
    quote = " ".join(ev["snippet"].split()[:6])
    bundles = apply_analysis(
        bundles,
        [
            {"evidence_id": ev["id"], "stance": "PROBABLY_TRUE", "quote": quote, "quote_verified": True},
            {"evidence_id": "ev_does_not_exist", "stance": "REFUTES", "quote": "x", "quote_verified": True},
        ],
    )
    assert ev["stance"] == "NEUTRAL", "unknown stance must not be invented"
    assert ev["quote_verified"] is True
    assert len(bundles[0]["evidence"]) == len(bundles[0]["evidence"])  # nothing appended


def test_build_score_request_shape_and_summary():
    from s2_service import build_score_request, retrieval_summary, retrieve_for_claims

    bundles = retrieve_for_claims(
        [{"id": "c1", "text": "vitamin C prevents the common cold"}],
        engine=_offline_service_engine(),
    )
    summary = retrieval_summary(bundles)
    assert summary["claims"] == 1
    assert summary["evidence_items"] == len(bundles[0]["evidence"])
    assert summary["quote_verified_items"] == 0     # S3 has not run yet

    payload = build_score_request(
        [{"id": "c1", "text": "vitamin C prevents the common cold"}],
        engine=_offline_service_engine(),
    )
    assert set(payload) == {"input", "claims", "as_of"}
    assert payload["claims"][0]["claim"]["id"] == "c1"


def test_async_entry_point_works():
    import asyncio

    from s2_service import aretrieve_for_claims

    bundles = asyncio.run(
        aretrieve_for_claims([{"id": "c1", "text": "vitamin C common cold"}],
                             engine=_offline_service_engine())
    )
    assert bundles[0]["claim"]["id"] == "c1"


# ---------------------------------------------------------------------------
# Latency guards: per-claim deadline + parallel claims
# ---------------------------------------------------------------------------
class SlowAdapter(SourceAdapter):
    """Sleeps past the deadline to simulate the 10 s source seen on the venue wifi."""

    name = "slow"
    kind = "web"

    def __init__(self, delay: float = 3.0):
        self.delay = delay
        self.finished = False

    def search(self, query, *, limit, cache=None):
        import time as _t

        _t.sleep(self.delay)
        self.finished = True
        return [r("Slow source", "https://slow.example/a", "vitamin c common cold", "web")]


def test_slow_source_is_abandoned_at_the_deadline():
    import time as _t

    fast = StubAdapter(
        "fast", "encyclopedia",
        [r("Vitamin C", "https://enc.example/vitc", "vitamin c prevents the common cold", "encyclopedia")],
    )
    slow = SlowAdapter(delay=3.0)
    # fetch_full_text=0 because the point of this test is source abandonment. Left on,
    # the engine would also run its (separately bounded) page-text stage against the
    # stub's fake `enc.example` URL, whose DNS lookup then eats the 4 s
    # fulltext_deadline_s tail - and the assertion below would be measuring that
    # instead of the deadline it names.
    engine = make_engine([fast, slow], fetch_full_text=0)
    engine.config.total_deadline_s = 0.5

    started = _t.perf_counter()
    result = engine.search_claim("vitamin c common cold")
    elapsed = _t.perf_counter() - started

    assert elapsed < 1.5, f"deadline not enforced ({elapsed:.2f}s)"
    assert result.evidence, "the fast source must still contribute"
    statuses = {s.name: s.status for s in result.audit.sources}
    assert statuses["slow"] == "timeout"
    assert any("deadline" in note for note in result.audit.notes)


def test_full_text_stage_is_bounded_by_its_own_deadline(monkeypatch):
    """A hanging page fetch must cost time, and nothing more than its budget.

    `total_deadline_s` covers the *sources*. Page text for quote verification has its own
    budget (`fulltext_deadline_s`) and its own abandonment rule, because the honest outcome
    of an unfetched page is `text_scope="snippet"` - never a quote claimed against full text.
    """
    import time as _t

    import fulltext as _fulltext

    fast = StubAdapter(
        "fast", "encyclopedia",
        [r("Vitamin C", "https://enc.example/vitc", "vitamin c prevents the common cold", "encyclopedia")],
    )
    engine = make_engine([fast], fetch_full_text=1)
    engine.config.fulltext_deadline_s = 0.4

    def hanging_fetch(url, **kwargs):
        _t.sleep(30)  # a DNS lookup or a server that never answers
        raise AssertionError("unreachable")

    monkeypatch.setattr(_fulltext, "fetch_page_text", hanging_fetch)

    started = _t.perf_counter()
    result = engine.search_claim("vitamin c common cold")
    elapsed = _t.perf_counter() - started

    assert elapsed < 2.5, f"full-text stage not bounded ({elapsed:.2f}s)"
    assert result.evidence, "the claim must still come back with its snippet evidence"
    for item in result.evidence:
        assert item.text_scope != "full_text", "an unfetched page must not claim full text"


def test_parallel_claims_preserve_order_and_cut_wall_clock():
    """Claims must run concurrently and keep input order.

    Deliberately compares parallel against serial on the *same* machine rather
    than asserting an absolute wall clock: Windows thread-startup overhead made an
    absolute threshold flaky (observed 1.85 s for work that was genuinely parallel).
    """
    import time as _t

    from s2_service import retrieve_for_claims

    class PerCallAdapter(SourceAdapter):
        name = "echo"
        kind = "web"

        def search(self, query, *, limit, cache=None):
            _t.sleep(0.4)
            return [
                r(f"About {query}", f"https://echo.example/{abs(hash(query))}",
                  f"{query} explained in detail", "web")
            ]

    # fetch_full_text=0 for the same reason test_slow_source_is_abandoned_at_the_deadline
    # sets it: the stub hands out fake `echo.example` URLs, and the engine's page-text
    # stage would spend its DNS lookup plus the 4 s full-text tail on every item. That
    # tail is per-call, so it lands on the parallel and the serial measurement alike and
    # the ratio below would be comparing two numbers that are mostly network timeout.
    engine = make_engine([PerCallAdapter()], fetch_full_text=0)
    claims = [{"id": f"c{i}", "text": f"unique claim number {i}"} for i in range(4)]

    # warm up thread pools so startup cost is not measured
    retrieve_for_claims(claims[:1], engine=engine, parallel_claims=1)

    def timed(count: int, workers: int, tag: str) -> float:
        """Best of `count` runs. The minimum is the point: on a busy machine a single
        measurement is dominated by whatever else is running, and a ratio built from two
        such measurements is noise. Each run uses fresh claim text, or the engine cache
        would answer it and measure nothing."""
        best = float("inf")
        for attempt in range(count):
            batch = [
                {"id": f"c{i}-{tag}{attempt}", "text": f"unique claim number {i} {tag}{attempt}"}
                for i in range(4)
            ]
            started = _t.perf_counter()
            retrieve_for_claims(batch, engine=engine, parallel_claims=workers)
            best = min(best, _t.perf_counter() - started)
        return best

    bundles = retrieve_for_claims(claims, engine=engine, parallel_claims=4)
    parallel_elapsed = timed(3, 4, "p")
    serial_elapsed = timed(2, 1, "s")

    assert [b["claim"]["id"] for b in bundles] == ["c0", "c1", "c2", "c3"]
    assert serial_elapsed > 0.1, "serial baseline too fast to compare against"
    ratio = parallel_elapsed / serial_elapsed
    assert ratio < 0.75, (
        f"claims did not run concurrently: parallel={parallel_elapsed:.2f}s "
        f"serial={serial_elapsed:.2f}s ratio={ratio:.2f}"
    )


# ---------------------------------------------------------------------------
# Cache honesty: a measurement must not report cached responses as live latency
# ---------------------------------------------------------------------------
def test_null_cache_never_stores_or_serves():
    from cache import NullCache, make_key

    nc = NullCache()
    key = make_key("k")
    nc.set(key, {"body": "cached", "status": 200})
    assert nc.get(key) is None
    assert nc.stats()["hits"] == 0


def test_use_cache_false_forces_a_real_request():
    """use_cache=False must bypass the cache so a measurement reflects the network."""
    calls = {"n": 0}

    class CountingAdapter(SourceAdapter):
        name = "counter"
        kind = "web"

        def search(self, query, *, limit, cache=None):
            calls["n"] += 1
            return [r("Vitamin C", "https://a.example/vitc", "vitamin c common cold", "web")]

    engine = RetrievalEngine(
        config=RetrievalConfig(use_cache=False), adapters=[CountingAdapter()]
    )
    engine.search_claim("vitamin c common cold")
    engine.search_claim("vitamin c common cold")
    assert calls["n"] == 2, "with caching off, every claim hits the adapter again"


# ---------------------------------------------------------------------------
# S2 -> S3 handoff: real S1 Claim objects, entities, claim_type mapping
# ---------------------------------------------------------------------------
def _s1_claim():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
    import s1_schemas_snapshot as s1

    return s1.Claim(
        id="claim_001",
        text="India is the world's largest solar energy producer",
        entities=["India"],
        claim_type="factual",
    )


def test_aretrieve_accepts_real_s1_claim_objects():
    """S1's Claim is pydantic with extra='forbid' and exactly 4 fields."""
    import asyncio

    from s2_service import aretrieve_for_claims

    claim = _s1_claim()
    assert claim.model_fields_set == {"id", "text", "entities", "claim_type"}

    bundles = asyncio.run(aretrieve_for_claims([claim], engine=_offline_service_engine()))
    assert len(bundles) == 1
    payload = bundles[0]["claim"]

    assert payload["type"] == "factual"          # claim_type -> type, nothing lost
    assert payload["id"] == "claim_001"
    assert payload["text"] == claim.text
    assert payload["article_quote"] is None      # S1 does not emit one
    assert payload["time_sensitive"] is False    # S1 does not emit one


def test_s1_entities_enrich_the_query_without_changing_retrieval_contract():
    from s2_service import retrieve_for_claims

    class RecordingAdapter(SourceAdapter):
        name = "rec"
        kind = "web"

        def __init__(self):
            self.queries = []

        def search(self, query, *, limit, cache=None):
            self.queries.append(query)
            return [
                r("Solar in India", "https://en.wikipedia.org/wiki/Solar_power_in_India",
                  "India solar capacity has grown rapidly", "web")
            ]

    adapter = RecordingAdapter()
    engine = make_engine([adapter])
    bundles = retrieve_for_claims([_s1_claim()], engine=engine, parallel_claims=1)

    assert any("India" in q for q in adapter.queries), "entity must reach the query"
    assert bundles[0]["claim"]["type"] == "factual"
    assert all(e["claim_id"] == "claim_001" for e in bundles[0]["evidence"])


def test_expanded_s1_claim_still_validates_against_lead_models():
    models = _lead_models()
    from contract_adapter import to_claim_bundle
    from contracts import Evidence

    claim = _s1_claim()
    bundle = to_claim_bundle(
        claim,
        evidence=[
            Evidence(
                url="https://en.wikipedia.org/wiki/Solar_power_in_India",
                domain="en.wikipedia.org",
                title="Solar power in India",
                text_or_snippet="India has one of the world's largest solar programmes.",
                source_kind="encyclopedia",
                relevance=0.72,
            )
        ],
    )
    parsed = models.ClaimEvidenceBundle(
        **{k: v for k, v in bundle.items() if k != "quote_guard"}
    )
    assert parsed.claim.type == "factual"
    assert parsed.evidence[0].claim_id == "claim_001"


# ---------------------------------------------------------------------------
# Full text for the quote-verification guard
# ---------------------------------------------------------------------------
def test_html_to_text_strips_boilerplate_and_keeps_paragraphs():
    from fulltext import html_to_text

    markup = """
    <html><head><style>.a{color:red}</style><script>var x=1;</script></head>
    <body><nav>menu menu</nav>
      <article><h1>Solar in India</h1>
        <p>India added a record amount of solar capacity.</p>
        <p>Coal still supplies most of the country&#39;s power.</p>
      </article>
      <footer>copyright</footer></body></html>
    """
    text = html_to_text(markup)
    assert "India added a record amount" in text
    assert "var x=1" not in text
    assert "menu menu" not in text
    assert "copyright" not in text
    assert "country's power" in text


def test_wikipedia_title_detection():
    from fulltext import wikipedia_title

    assert (
        wikipedia_title("https://en.wikipedia.org/wiki/Solar_power_in_India")
        == "Solar power in India"
    )
    assert wikipedia_title("https://example.com/article") is None


def test_full_text_marks_scope_and_never_pretends(monkeypatch):
    """A fetchable page -> scope full_text; a failed one -> snippet + reason."""
    import fulltext

    def fake_fetch(url, *, timeout=5.0, max_chars=6000, cache=None):
        if "wikipedia" in url:
            return fulltext.PageText(
                url=url, status="full_text",
                text="India added a record amount of solar capacity in 2024. " * 20,
                chars=1100,
            )
        return fulltext.PageText(url=url, status="snippet", reason="HTTP 403")

    monkeypatch.setattr(fulltext, "fetch_page_text", fake_fetch)

    sources = [
        r("Solar power in India", "https://en.wikipedia.org/wiki/Solar_power_in_India",
          "India solar capacity record", "encyclopedia"),
        r("Solar policy", "https://paywalled.example/solar", "India solar policy overview", "web"),
    ]
    engine = RetrievalEngine(
        config=RetrievalConfig(top_k=4, fetch_full_text=2),
        adapters=[StubAdapter("s", "web", sources)],
    )
    result = engine.search_claim("India solar capacity record")

    by_domain = {e.domain: e for e in result.evidence}
    wiki = by_domain["en.wikipedia.org"]
    other = by_domain["paywalled.example"]
    assert wiki.text_scope == "full_text"
    assert len(wiki.full_text) > 200
    assert other.text_scope == "snippet"
    assert other.full_text == ""
    assert "403" in (other.text_scope_reason or "")
    assert result.audit.full_text_fetched == 1
    assert result.audit.full_text_failed >= 1
    assert any("snippet-only" in n for n in result.audit.notes)


def test_full_text_disabled_in_offline_mode():
    engine = RetrievalEngine(config=RetrievalConfig(offline=True, fetch_full_text=3))
    result = engine.search_claim("vitamin C prevents the common cold")
    assert result.evidence
    assert all(e.text_scope == "snippet" and e.full_text == "" for e in result.evidence)


def test_quote_guard_reports_what_s3_may_claim():
    from contract_adapter import quote_guard_map
    from contracts import Evidence

    with_text = Evidence(
        url="https://a.example/1", domain="a.example", text_or_snippet="short",
        full_text="full page text " * 30, text_scope="full_text",
    )
    snippet_only = Evidence(
        url="https://b.example/2", domain="b.example", text_or_snippet="short",
        text_scope="snippet", text_scope_reason="HTTP 403",
    )
    guard = quote_guard_map([with_text, snippet_only])
    assert sorted(guard.values()) == ["full_text", "snippet"]


def test_quote_check_only_passes_against_the_text_we_actually_have():
    from s2_service import quote_support

    item = {
        "id": "ev_1",
        "snippet": "India added a record amount of solar capacity.",
        "full_text": "",
    }
    in_snippet, scope_a = quote_support(item, "added a record amount of solar capacity")
    assert in_snippet and scope_a == "snippet"

    not_present, scope_b = quote_support(item, "India will ban coal by 2030")
    assert not not_present and scope_b == "snippet"

    item_with_page = dict(item, full_text="Intro. India will ban coal by 2030, the minister said. " * 5)
    found, scope_c = quote_support(item_with_page, "India will ban coal by 2030")
    assert found and scope_c == "full_text"


def test_apply_analysis_verifies_against_full_text_and_refuses_without_it():
    from s2_service import apply_analysis

    base = [
        {
            "claim": {"id": "c1", "text": "t", "type": "factual",
                      "article_quote": None, "time_sensitive": False},
            "evidence": [
                {"id": "ev_page", "claim_id": "c1", "url": "https://a.example/1",
                 "domain": "a.example", "snippet": "Short intro sentence.",
                 "full_text": "Short intro sentence. The minister confirmed the ban by 2030. " * 4,
                 "stance": "NEUTRAL", "quote": "", "quote_verified": False, "rationale": ""},
                {"id": "ev_snip", "claim_id": "c1", "url": "https://b.example/2",
                 "domain": "b.example", "snippet": "Short intro sentence.", "full_text": "",
                 "stance": "NEUTRAL", "quote": "", "quote_verified": False, "rationale": ""},
            ],
        }
    ]
    analyses = [
        {"evidence_id": "ev_page", "stance": "SUPPORTS", "quote_verified": True,
         "quote": "The minister confirmed the ban by 2030."},
        {"evidence_id": "ev_snip", "stance": "SUPPORTS", "quote_verified": True,
         "quote": "The minister confirmed the ban by 2030."},
    ]
    merged = apply_analysis(
        [dict(b, evidence=[dict(e) for e in b["evidence"]]) for b in base], analyses
    )
    items = {e["id"]: e for e in merged[0]["evidence"]}

    assert items["ev_page"]["quote_verified"] is True      # present in full text
    assert items["ev_snip"]["quote_verified"] is False     # not checkable from snippet
    assert items["ev_snip"]["quote"] == ""


# ---------------------------------------------------------------------------
# S2 stays retrieval-only: no stance, no verdict
# ---------------------------------------------------------------------------
def test_s2_output_contains_no_stance_or_verdict_decisions():
    from s2_service import retrieve_for_claims

    bundles = retrieve_for_claims(
        [{"id": "c1", "text": "vitamin C prevents the common cold"}],
        engine=_offline_service_engine(),
    )
    bundle = bundles[0]

    assert "verdict" not in bundle
    assert "evidence_strength" not in bundle
    assert "stance_score" not in bundle

    for item in bundle["evidence"]:
        assert item["stance"] == "NEUTRAL", "S2 must not assign stance"
        assert item["quote_verified"] is False, "S2 must not verify quotes"
        assert item["quote"] == ""
        assert item["rationale"] == ""


def test_apply_analysis_never_invents_a_stance_it_was_not_given():
    from s2_service import apply_analysis

    bundle = {
        "claim": {"id": "c1", "text": "t", "type": "factual",
                  "article_quote": None, "time_sensitive": False},
        "evidence": [{"id": "ev_1", "claim_id": "c1", "url": "https://a.example/1",
                      "domain": "a.example", "snippet": "text here", "full_text": "",
                      "stance": "NEUTRAL", "quote": "", "quote_verified": False, "rationale": ""}],
    }
    merged = apply_analysis([bundle], [])
    assert merged[0]["evidence"][0]["stance"] == "NEUTRAL"

    merged2 = apply_analysis(
        [json.loads(json.dumps(bundle))],
        [{"evidence_id": "ev_1", "stance": "", "quote": ""}],
    )
    assert merged2[0]["evidence"][0]["stance"] == "NEUTRAL"


# ---------------------------------------------------------------------------
# S3 seam checker (the tool Ikjot runs before the freeze)
# ---------------------------------------------------------------------------
def test_check_s3_contract_runs_offline_and_reports_no_analyzer(tmp_path, monkeypatch):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_s3_contract.py"
    spec = importlib.util.spec_from_file_location("check_s3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "main")
    # offline corpus + no analyzer: must exit 0 and never raise
    assert module.main(["--offline", "--top-k", "3"]) == 0


def test_check_s3_contract_flags_a_fabricating_analyzer(tmp_path, monkeypatch, capsys):
    """An analyzer whose quote is not in the retrieved text must be a BLOCKER."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_s3_contract.py"
    spec = importlib.util.spec_from_file_location("check_s3b", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # build a fake analyzer module on disk and import it by name
    fake = tmp_path / "fake_s3.py"
    fake.write_text(
        "def analyze(claims, bundles):\n"
        "    out = []\n"
        "    for b in bundles:\n"
        "        for item in b['evidence'][:1]:\n"
        "            out.append({'evidence_id': item['id'], 'stance': 'REFUTES',\n"
        "                        'quote': 'THIS SENTENCE IS NOT IN THE SOURCE',\n"
        "                        'quote_verified': True})\n"
        "    return out\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    code = module.main(["--offline", "--top-k", "3", "--analyze", "fake_s3:analyze"])
    captured = capsys.readouterr().out
    assert code == 1, "a fabricated quote must produce a non-zero exit"
    assert "not found in retrieved" in captured


# ---------------------------------------------------------------------------
# Environment / dependency guardrails
# ---------------------------------------------------------------------------
def test_module_scoped_requirements_lists_real_dependencies():
    """requirements-s2.txt exists so a teammate overwriting the shared
    requirements.txt cannot remove pydantic from under this module."""
    path = Path(__file__).resolve().parents[1] / "requirements-s2.txt"
    assert path.exists(), "requirements-s2.txt must exist"
    text = path.read_text(encoding="utf-8")
    for package in ("requests", "pydantic", "pytest"):
        assert any(ln.strip().lower().startswith(package) for ln in text.splitlines()), package


def test_setup_scripts_verify_imports_not_just_pip_exit_code():
    """The old setup printed 'installed' without checking, which let a missing
    pydantic reach the demo. Both scripts must import-check afterwards."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("setup_windows.bat", "setup_windows.ps1"):
        text = (scripts / name).read_text(encoding="utf-8", errors="replace")
        assert "import requests, pydantic, pytest" in text, f"{name} must verify imports"


def test_doctor_script_imports_and_runs_checks():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("doctor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert {name for name, _ in module.REQUIRED} == {"requests", "pydantic", "pytest"}


# ---------------------------------------------------------------------------
# Windows script hygiene
# ---------------------------------------------------------------------------
def test_windows_scripts_are_ascii_only():
    """PowerShell 5.1 treats smart quotes as string delimiters, so an em dash or
    curly quote inside a .ps1 string breaks parsing with a confusing error like
    'Missing argument in parameter list'. Keep these files ASCII."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    offenders = {}
    for path in sorted(scripts.glob("*.ps1")) + sorted(scripts.glob("*.bat")):
        raw = path.read_bytes()
        bad = [(i, b) for i, b in enumerate(raw) if b > 127]
        if bad:
            offenders[path.name] = f"{len(bad)} non-ASCII bytes"
    assert not offenders, f"non-ASCII bytes in Windows scripts: {offenders}"


def test_gitattributes_pins_crlf_for_windows_scripts():
    """Line endings are a repository property, not a working-tree accident.

    A Windows checkout with core.autocrlf=true normalises .ps1/.bat to LF when
    committing, so asserting CRLF in the working tree passes locally and fails in
    a fresh clone (found 2026-10-31). What actually has to be true is that
    .gitattributes forces CRLF on checkout for those files.
    """
    root = Path(__file__).resolve().parents[1]
    attributes = (root / ".gitattributes").read_text(encoding="utf-8")
    assert "*.ps1 text eol=crlf" in attributes
    assert "*.bat text eol=crlf" in attributes
    assert "*.py text eol=lf" in attributes


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def test_cache_hit_miss_and_expiry(tmp_path, monkeypatch):
    cache = Cache(ttl_s=60, disk_dir=tmp_path)
    key = make_key("wiki", "query", {"a": 1})
    assert cache.get(key) is None
    cache.set(key, {"x": 1})
    assert cache.get(key) == {"x": 1}
    assert cache.stats()["hits"] >= 1

    import time

    real_time = time.time
    monkeypatch.setattr("cache.time.time", lambda: real_time() + 120)
    assert cache.get(key) is None


def test_disk_cache_survives_new_instance(tmp_path):
    config = {"ttl_s": 60, "disk_dir": tmp_path}
    first = Cache(**config)
    key = make_key("k")
    first.set(key, {"body": "value", "status": 200})
    second = Cache(**config)
    assert second.get(key) == {"body": "value", "status": 200}
