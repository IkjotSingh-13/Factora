export type Tone = "support" | "refute" | "neutral" | "quiet";

export interface Score {
  label: string;
  value: number;
  weight: number;
  points: number;
  reason: string;
  inputs: string;
}

export interface Analysis {
  quote: string;
  quote_verified: boolean;
  /**
   * What the quote was matched against. "full_text" means the fetched page; "snippet"
   * means only the search-result text we hold, which is the weaker check and is said out
   * loud in the UI instead of being smoothed over.
   */
  quote_scope?: "full_text" | "snippet";
  quote_mode?: "exact" | "insertions";
  quote_insertions?: number;
  rationale: string;
  context_gap: string | null;
}

export interface Evidence {
  cluster_id: number;
  url: string;
  domain: string;
  title: string;
  text_or_snippet: string;
  published_at: string | null;
  source_kind: string;
  relevance: number;
  stance: "SUPPORTS" | "REFUTES" | "NEUTRAL";
  analysis: Analysis;
  fetched_at: string;
  word_count?: number;
}

export interface Claim {
  id: string;
  folio: string;
  text: string;
  type: string;
  article_quote: string;
  article_quote_position?: string;
  time_sensitive: boolean;
  entities: string[];
  verdict: { label: string; tone: Tone; one_line: string };
  stance_direction: {
    lean: "supports" | "refutes" | "unclear";
    counts: { supports: number; refutes: number; neutral: number };
    summary: string;
  };
  evidence_strength: number;
  strength_label?: string;
  scores: Record<"c" | "r" | "a" | "t" | "k", Score> & {
    s: { value: number; formula: string; result: string };
  };
  insufficient: { is: boolean; reasons: string[] };
  evidence: Evidence[];
  audit: {
    sources_queried: string[];
    results_retrieved: number;
    results_used: number;
    quotes_checked: number;
    quotes_dropped: number;
    claims_dropped: number;
    notes: string[];
  };
}

export interface Report {
  schema_version: string;
  mode: "mock" | "live";
  demo_label?: string;
  fixture_note?: string;
  run: {
    run_id: string;
    mode: string;
    engine: string;
    started_at: string;
    finished_at: string;
    duration_ms: number;
    adapters?: { name: string; kind: string; status: string; results: number }[];
  };
  article: {
    title: string;
    origin: string;
    source_url: string | null;
    words: number;
    submitted_at: string;
    excerpt?: string;
  };
  disclaimer: string;
  summary: {
    claims_total: number;
    claims_checked: number;
    status_counts: Partial<Record<"supported" | "contested" | "refuted" | "insufficient", number>>;
    evidence_items: number;
    evidence_clusters: number;
    sources_used: number;
    sources_failed: number;
    quotes_dropped: number;
    stance_counts: { supports: number; refutes: number; neutral: number };
  };
  method: {
    stages: { id: string; name: string; engine: string; detail: string }[];
    omitted: { name: string; reason: string }[];
  };
  default_claim_id: string;
  claims: Claim[];
  limitations: string[];
  failed_sources: { name: string; status: string; detail: string; attempts: number }[];
}
