/**
 * Real pipeline client.
 *
 * The visual layer (types.ts, the components) was written against the fixture
 * schema in `src/data/*.json`. This module is the only place that talks to the
 * backend: it POSTs to `/api/verify` and maps the response onto that schema.
 *
 * Two rules, because they are what the product is for:
 *   1. nothing is displayed as evidence that the API did not return. No invented
 *      quotes, no invented URLs, no invented scores — every field below is either
 *      copied from the response or computed from numbers in it.
 *   2. when a run is not a live retrieval, the label says so (`mode`, `demo_label`,
 *      `limitations`) instead of letting a cached or offline report look like evidence.
 */
import type { Claim, Evidence, Report, Score, Tone } from "../types";

export type EvidenceMode = "auto" | "live" | "offline" | "snapshot";
type Dict = Record<string, any>;

const WEIGHTS: Record<"c" | "r" | "a" | "t" | "k", number> = {
  c: 0.3,
  r: 0.3,
  a: 0.2,
  t: 0.1,
  k: 0.1,
};

const KIND_LABELS: Record<string, string> = {
  encyclopedia: "Encyclopedia",
  government: "Government agency",
  health: "Health authority",
  news: "News wire",
  wire: "News wire",
  fact_check: "Professional fact-check",
  scientific: "Peer-reviewed / preprint",
  trade: "Trade press",
  mock: "Local demo corpus",
};

export const kindLabel = (kind?: string | null): string => {
  if (!kind) return "Unclassified source";
  return KIND_LABELS[kind] || kind.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
};

export class PipelineError extends Error {}

const num = (v: any, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const round = (v: number, d = 3): number => Math.round(v * 10 ** d) / 10 ** d;

function toneFor(verdict: string): Tone {
  const v = (verdict || "").toUpperCase();
  if (v.startsWith("SUPPORTED")) return "support";
  if (v.startsWith("REFUTED")) return "refute";
  if (v.includes("CONFLICT")) return "neutral";
  if (v.includes("MIXED")) return "neutral";
  return "quiet";
}

function strengthWord(value: number): string {
  if (value >= 0.75) return "High";
  if (value >= 0.55) return "Moderate";
  if (value >= 0.45) return "Thin";
  return "Weak";
}

/** Position of the article quote inside what the user submitted — counted, not guessed. */
function quotePosition(text: string, quote: string): string | undefined {
  if (!text || !quote) return undefined;
  const at = text.indexOf(quote);
  if (at < 0) return undefined;
  const before = text.slice(0, at);
  const paragraphs = before.split(/\n\s*\n/).filter((p) => p.trim()).length || 1;
  const inParagraph = before.slice(before.lastIndexOf("\n\n") + 2);
  const sentences = inParagraph.split(/(?<=[.?!])\s+/).filter((s) => s.trim()).length + 1;
  return `Paragraph ${paragraphs}, sentence ${sentences}`;
}

function scoreEntry(
  key: "c" | "r" | "a" | "t" | "k",
  label: string,
  values: Dict,
  contributions: Dict,
  reason: string,
  inputs: string
): Score {
  const value = round(num(values[key.toUpperCase()] ?? values[key]));
  const points = round(num(contributions[key.toUpperCase()] ?? contributions[key]), 4);
  return { label, value, weight: WEIGHTS[key], points, reason, inputs };
}

function mapEvidence(raws: Dict[], claimText: string): Evidence[] {
  const clusters = new Map<string, number>();
  return (raws || []).map((ev, index) => {
    const domain = ev.domain || (ev.url ? String(ev.url).split("/")[2] || "unknown" : "unknown");
    if (!clusters.has(domain)) clusters.set(domain, clusters.size + 1);
    const body = (ev.full_text || ev.snippet || "").trim();
    const verified = Boolean(ev.quote_verified) && Boolean(ev.quote);
    return {
      cluster_id: clusters.get(domain) as number,
      url: ev.url || "",
      domain,
      title: ev.title || domain,
      // what we actually hold for this source, never more than that
      text_or_snippet: body || (verified ? ev.quote : claimText),
      published_at: ev.published_at || null,
      source_kind: kindLabel(ev.source_kind),
      relevance: round(num(ev.relevance), 4),
      stance: (ev.stance === "REFUTES" ? "REFUTES" : ev.stance === "SUPPORTS" ? "SUPPORTS" : "NEUTRAL") as Evidence["stance"],
      analysis: {
        // the quote is copied verbatim from the response or not shown at all
        quote: verified ? String(ev.quote) : "",
        quote_verified: verified,
        // `quote_mode` describes *how* the quote matched, not what we fetched it against;
        // mixing the two up here would make a snippet match look like an edited quote.
        quote_mode: "exact",
        quote_scope: ev.quote_scope === "full_text" ? "full_text" : "snippet",
        quote_insertions: 0,
        rationale:
          ev.rationale ||
          (verified
            ? ev.quote_scope === "full_text"
              ? "Matched verbatim against the fetched page text."
              : "Matched verbatim against the retrieved search snippet, not the fetched page - the weaker check, labelled as such."
            : "No passage in the retrieved text matched this claim, so nothing is quoted."),
        context_gap: ev.context_gap || null,
      },
      fetched_at: new Date().toISOString(),
      word_count: body ? body.split(/\s+/).filter(Boolean).length : undefined,
    } as Evidence;
  });
}

function mapClaim(raw: Dict, index: number, articleText: string, warnings: string[], grounding: Dict[]): Claim {
  const claim = raw.claim || {};
  const audit = raw.audit || {};
  const evidence = mapEvidence(raw.evidence || [], claim.text || "");
  const values = raw.scores || {};
  const contributions = raw.contributions || [];
  const used = num(audit.n_used);
  const retrieved = num(audit.n_retrieved);
  const independent = num(audit.n_independent);
  const strength = round(num(raw.evidence_strength));
  const scoreRaw = round(num(raw.score_raw));
  const counts = evidence.reduce(
    (acc, ev) => {
      if (ev.stance === "SUPPORTS") acc.supports += 1;
      else if (ev.stance === "REFUTES") acc.refutes += 1;
      else acc.neutral += 1;
      return acc;
    },
    { supports: 0, refutes: 0, neutral: 0 }
  );

  const domains = Array.from(new Set(evidence.map((ev) => ev.domain)));
  const credibility = evidence.length
    ? round(
        evidence.reduce((sum, ev, i) => sum + num((raw.evidence || [])[i]?.credibility), 0) / evidence.length,
        3
      )
    : 0;
  const dates = evidence.map((ev) => ev.published_at).filter(Boolean).sort() as string[];
  const medianDate = dates.length ? dates[Math.floor((dates.length - 1) / 2)] : null;
  const gaps = evidence.filter((ev) => ev.analysis.context_gap).length;
  const snippetOnly = evidence.filter((ev) => ev.analysis.quote_mode !== "exact" && ev.analysis.quote_verified).length;
  const ground = (grounding || []).find((g) => g.id === claim.id) || {};

  const lean: Claim["stance_direction"]["lean"] =
    num(raw.stance_score) > 0.15 ? "supports" : num(raw.stance_score) < -0.15 ? "refutes" : "unclear";

  const insufficientReasons: string[] = [];
  if (used < 2) insufficientReasons.push(`Only ${used} quote-verified item(s) could be scored, and the floor is 2.`);
  if (independent < 2) insufficientReasons.push(`${independent} independent domain(s) answered, and a decisive verdict needs 2.`);
  if (strength < 0.45) insufficientReasons.push(`Evidence strength ${strength} is below the 0.45 decision floor.`);

  const inputs = evidence
    .slice(0, 4)
    .map((ev, i) => `${ev.domain} (${ev.source_kind.toLowerCase()}, cred ${round(num((raw.evidence || [])[i]?.credibility), 2)})`)
    .join(", ");

  const oneLineParts = [
    used
      ? `${used} of ${retrieved || used} retrieved passage(s) quote verbatim into ${
          snippetOnly ? `the retrieved snippet${snippetOnly === 1 ? "" : "s"}` : "the fetched page text"
        }`
      : "No retrieved passage could be quoted verbatim",
    `${independent} independent domain(s)`,
  ];
  if (counts.refutes) oneLineParts.push(`${counts.refutes} contradict the claim`);
  else oneLineParts.push("nothing in the evidence contradicts it");

  return {
    id: claim.id || `claim_${index + 1}`,
    folio: String(index + 1).padStart(2, "0"),
    text: claim.text || "",
    type: (claim.type || "factual").replace(/^\w/, (c: string) => c.toUpperCase()),
    article_quote: claim.article_quote || "",
    article_quote_position: quotePosition(articleText, claim.article_quote || ""),
    time_sensitive: Boolean(claim.time_sensitive),
    entities: ground.entities || [],
    verdict: {
      label: (raw.verdict || "INSUFFICIENT EVIDENCE").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c: string) => c.toUpperCase()),
      tone: toneFor(raw.verdict || ""),
      one_line: oneLineParts.join("; ") + ".",
    },
    stance_direction: {
      lean,
      counts,
      summary: `${counts.supports} supporting, ${counts.refutes} refuting, ${counts.neutral} contextual`,
    },
    evidence_strength: strength,
    strength_label: strengthWord(strength),
    scores: {
      c: scoreEntry(
        "c",
        "Source credibility",
        values,
        contributions,
        used ? `Mean credibility of the ${used} scored item(s) is ${credibility}.` : "Nothing was scored, so credibility is undefined here.",
        inputs
      ),
      r: scoreEntry(
        "r",
        "Evidence relevance",
        values,
        contributions,
        used
          ? `Credibility-weighted relevance across ${used} item(s); ${snippetOnly} verified against a snippet only.`
          : "No quote-verified item to weight.",
        evidence.slice(0, 4).map((ev) => `relevance ${ev.relevance} (${ev.domain})`).join(", ")
      ),
      a: scoreEntry(
        "a",
        "Independent agreement",
        values,
        contributions,
        `${domains.length} distinct domain(s) among ${used} scored item(s); agreement saturates at 2 independent domains.`,
        domains.join(", ")
      ),
      t: scoreEntry(
        "t",
        "Freshness",
        values,
        contributions,
        medianDate
          ? `Median publication date ${medianDate}${claim.time_sensitive ? " (claim is time-sensitive: 18-month half-life)" : ""}.`
          : `No date on any item; ${audit.unknown_freshness ? "freshness recorded as unknown." : "not date-sensitive."}`,
        dates.slice(0, 4).join(", ")
      ),
      k: scoreEntry(
        "k",
        "Contextual consistency",
        values,
        contributions,
        `${gaps} scored item(s) carry a context gap; ${counts.neutral} were left neutral because the passage does not settle it.`,
        evidence
          .filter((ev) => ev.analysis.context_gap)
          .slice(0, 2)
          .map((ev) => `${ev.domain}: ${String(ev.analysis.context_gap).slice(0, 60)}`)
          .join(" | ")
      ),
      s: {
        value: scoreRaw,
        formula: "0.30 C + 0.30 R + 0.20 A + 0.10 T + 0.10 K",
        result: `${[
          round(num(contributions.C ?? contributions.c), 3),
          round(num(contributions.R ?? contributions.r), 3),
          round(num(contributions.A ?? contributions.a), 3),
          round(num(contributions.T ?? contributions.t), 3),
          round(num(contributions.K ?? contributions.k), 3),
        ].join(" + ")} = ${scoreRaw}`,
      },
    } as Claim["scores"],
    // ClaimDetail joins these with ". " and adds no punctuation of its own, so each reason
    // is handed over without a trailing full stop.
    insufficient: {
      is: insufficientReasons.length > 0,
      reasons: insufficientReasons.map((r) => r.replace(/\.\s*$/, "")),
    },
    evidence,
    audit: {
      sources_queried: domains,
      results_retrieved: retrieved,
      results_used: used,
      quotes_checked: num((raw.evidence || []).length),
      quotes_dropped: num(audit.hallucinated_quotes_dropped),
      claims_dropped: 0,
      notes: warnings.slice(0, 6),
    },
  };
}

/** Map one `/api/verify` response onto the report schema the components render. */
export function mapResponse(data: Dict, submittedText: string, sourceUrl: string | null): Report {
  const report = data.report || {};
  const claims: Dict[] = report.claims || [];
  const warnings: string[] = data.warnings || [];
  const grounding: Dict[] = (data.grounding && data.grounding.claims) || [];
  const summary = data.retrieval_summary || {};
  const s3 = data.s3_audit || {};
  const s1 = data.s1 || {};
  const mode: string = data.mode || "offline";
  const stages: Dict[] = data.stages || [];
  const durationMs = stages.reduce((total, st) => total + num(st.ms), 0);
  const finishedAt = new Date();
  const startedAt = new Date(finishedAt.getTime() - durationMs);

  const statusCounts: Record<string, number> = {};
  const stanceCounts = { supports: 0, refutes: 0, neutral: 0 };
  let quotesDropped = 0;
  let evidenceItems = 0;
  for (const claim of claims) {
    const label = String(claim.verdict || "").toUpperCase();
    const bucket = label.startsWith("SUPPORTED")
      ? "supported"
      : label.startsWith("REFUTED")
      ? "refuted"
      : label.includes("CONFLICT") || label.includes("MIXED")
      ? "contested"
      : "insufficient";
    statusCounts[bucket] = (statusCounts[bucket] || 0) + 1;
    quotesDropped += num(claim.audit?.hallucinated_quotes_dropped);
    for (const ev of claim.evidence || []) {
      evidenceItems += 1;
      if (ev.stance === "SUPPORTS") stanceCounts.supports += 1;
      else if (ev.stance === "REFUTES") stanceCounts.refutes += 1;
      else stanceCounts.neutral += 1;
    }
  }

  const adapters = new Map<string, { name: string; kind: string; status: string; results: number }>();
  for (const detail of data.per_claim_retrieval || []) {
    for (const src of detail.sources || []) {
      const key = src.domain || String(src.url || "").split("/")[2] || "unknown";
      const row = adapters.get(key) || { name: key, kind: kindLabel(src.source_kind), status: "ok", results: 0 };
      row.results += 1;
      adapters.set(key, row);
    }
  }

  const failed: Report["failed_sources"] = [];
  for (const claim of claims) {
    for (const entry of claim.audit?.sources_failed || []) {
      const text = typeof entry === "string" ? entry : JSON.stringify(entry);
      const [name, ...rest] = text.split(":");
      failed.push({ name: (name || "source").trim(), status: "failed", detail: rest.join(":").trim() || text, attempts: 1 });
    }
  }

  // The backend already writes per-run caveats into `warnings[]`. They are quoted first and
  // verbatim so the UI can never drift out of sync with what the pipeline actually did.
  const runWarnings: string[] = Array.isArray(data.warnings)
    ? data.warnings.filter((w: any) => typeof w === "string" && w.trim().length > 0)
    : [];
  const limitations: string[] = [
    ...runWarnings,
    "Only passages whose quote appears verbatim in retrieved text are scored; a quote that fails is dropped and counted, never displayed.",
    "Evidence strength measures how much independent, relevant, quote-verifiable material was found. It is not the probability that a claim is true.",
  ];
  if (s3.analyzer !== "llm") {
    limitations.push(
      `Stances came from the deterministic lexical analyser (${
        s3.provider_note || "no LLM configured"
      }), not a trained NLI model, so a passage that merely mentions a number can mislead it.`
    );
  }
  if (num(summary.snippet_only_items) > 0) {
    limitations.push(
      `${summary.snippet_only_items} item(s) were verified against the retrieved snippet rather than the fetched page, which is a weaker check and is labelled as such.`
    );
  }
  if (mode === "offline") {
    limitations.push(
      "This run used the local demo corpus only. Its entries are tagged source_kind=mock at credibility 0.10 by design, so scores stay low: the mechanism is on display, not real evidence."
    );
  }
  if (mode === "snapshot") {
    limitations.push("This run replayed a captured snapshot: the retrieval happened earlier, on a different machine, and is not a live lookup.");
  }
  limitations.push(
    `Claim extraction ran on the "${s1.provider || "mock"}" provider${s1.used_fallback ? " with the heuristic fallback in use" : ""}.`
  );

  const firstSentence = (submittedText || "").trim().split(/(?<=[.?!])\s/)[0] || "";

  return {
    schema_version: "1.0",
    // "mock" is this schema's word for "not a live retrieval"; the label says which
    mode: mode === "live" ? "live" : "mock",
    demo_label:
      mode === "live"
        ? "Live pipeline run"
        : mode === "snapshot"
        ? "DEMO BACKUP - captured retrieval, replayed"
        : "Offline run against the local demo corpus",
    fixture_note:
      mode === "live"
        ? undefined
        : `mode=${mode}. No live source was queried for this report; the numbers below come from ${
            mode === "snapshot" ? "a captured retrieval" : "data/mock_corpus.json"
          }.`,
    run: {
      run_id: `FA-${String(finishedAt.getMonth() + 1).padStart(2, "0")}${String(finishedAt.getDate()).padStart(2, "0")}-${String(
        finishedAt.getHours()
      ).padStart(2, "0")}${String(finishedAt.getMinutes()).padStart(2, "0")}`,
      mode,
      engine: `FastAPI /api/verify · S1 ${s1.provider || "mock"} · S3 ${s3.analyzer || "lexical"}`,
      started_at: startedAt.toISOString(),
      finished_at: finishedAt.toISOString(),
      duration_ms: durationMs,
      adapters: Array.from(adapters.values()),
    },
    article: {
      title: sourceUrl ? `Analysis of ${sourceUrl.replace(/^https?:\/\/(www\.)?/, "").split("/")[0]}` : firstSentence.slice(0, 90) || "Pasted content",
      origin: sourceUrl ? new URL(sourceUrl.startsWith("http") ? sourceUrl : `https://${sourceUrl}`).hostname : "Pasted text",
      source_url: sourceUrl,
      words: (submittedText || "").split(/\s+/).filter(Boolean).length,
      submitted_at: startedAt.toISOString(),
      excerpt: firstSentence.slice(0, 200) || undefined,
    },
    disclaimer:
      report.disclaimer ||
      "Evidence-weighted assessment, not a guaranteed truth verdict. Review the cited sources and context.",
    summary: {
      claims_total: claims.length,
      claims_checked: claims.filter((claim) => String(claim.verdict || "").toUpperCase() !== "INSUFFICIENT EVIDENCE").length,
      status_counts: statusCounts as Report["summary"]["status_counts"],
      evidence_items: num(summary.evidence_items, evidenceItems),
      evidence_clusters: num(summary.distinct_domains),
      sources_used: adapters.size,
      sources_failed: failed.length,
      quotes_dropped: quotesDropped,
      stance_counts: stanceCounts,
    },
    method: {
      stages: stages.map((st, i) => ({
        id: String(st.name || "").split(" ")[0] || `S${i + 1}`,
        name: st.name || "stage",
        engine:
          String(st.name || "").startsWith("S1") && !String(st.name || "").includes("guard")
            ? `${s1.provider || "mock"} provider`
            : String(st.name || "").includes("S2")
            ? `${mode} retrieval`
            : String(st.name || "").includes("S3")
            ? `${s3.analyzer || "lexical"} analyser`
            : "exact arithmetic",
        detail: `${st.detail || ""}${st.ms != null ? ` · ${st.ms} ms · ${st.status}` : ""}`.trim(),
      })),
      omitted: [
        { name: "Cross-encoder reranking", reason: "Implemented in ranking.py but off by default; BM25 + embedding similarity + RRF do the ranking." },
        { name: "Vector database", reason: "No persistent store: the session corpus is rebuilt per run, and Qdrant stayed on the cut list." },
        ...(s3.analyzer !== "llm"
          ? [{ name: "LLM stance analysis", reason: `s3_audit.analyzer=${s3.analyzer || "lexical"} (${s3.provider_note || "no provider"}).` }]
          : []),
        ...(s1.provider === "mock"
          ? [{ name: "Model-backed extraction", reason: "LLM_PROVIDER=mock, so the deterministic extractor ran instead." }]
          : []),
      ],
    },
    default_claim_id: claims.length ? claims[0].claim?.id || "" : "",
    claims: claims.map((claim, i) => mapClaim(claim, i, submittedText, warnings, grounding)),
    limitations,
    failed_sources: failed,
  };
}

/**
 * Ask the pipeline to verify something. Replaces the client-side stand-in that used to
 * live here: that version invented Wikipedia and Reuters URLs and marked invented
 * sentences as `quote_verified`, which is the one thing this product must never do.
 */
export async function verifyInput(
  input: string,
  mode: "text" | "url",
  evidenceMode: EvidenceMode = "auto"
): Promise<{ report: Report; raw: Dict }> {
  const trimmed = input.trim();
  const body = mode === "url" ? { url: trimmed, mode: evidenceMode } : { article_text: trimmed, mode: evidenceMode };

  let res: Response;
  try {
    res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new PipelineError(
      "The API is not reachable. Start it with `python -m uvicorn app.main:app --app-dir backend --port 8000` and reload, or open /lite for the build that needs no npm."
    );
  }

  const text = await res.text();
  let data: Dict = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    /* keep the raw text for the message below */
  }
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : text.slice(0, 300) || `HTTP ${res.status}`;
    throw new PipelineError(`HTTP ${res.status} · ${detail}`);
  }
  if (!data.report) {
    throw new PipelineError("The response carried no report.");
  }
  return { report: mapResponse(data, mode === "text" ? trimmed : "", mode === "url" ? trimmed : null), raw: data };
}
