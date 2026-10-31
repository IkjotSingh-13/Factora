/*
 * UI render check (no browser needed).
 *
 * Extracts the inline script from frontend/index.html, evaluates it against a minimal
 * DOM stub, and runs a REAL /api/verify payload through its render functions. That
 * executes the same string-building the browser executes, so a broken claim card or a
 * missing audit field fails here rather than in front of a judge.
 *
 * Reads data/ui_response_fixture.json by default (a captured real response), or pass a
 * file to check the UI against a fresh one:
 *
 *   curl -s localhost:8000/api/verify -H 'content-type: application/json' \
 *        -d '{"article_text":"...","mode":"offline"}' > /tmp/live.json
 *   node scripts/ui_render_check.mjs /tmp/live.json
 *
 * Exits non-zero if the script cannot be evaluated or a required element is missing
 * from the generated markup.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "frontend", "index.html"), "utf8");

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (scripts.length !== 1) {
  console.error(`expected exactly one inline <script>, found ${scripts.length}`);
  process.exit(1);
}
const source = scripts[0];

/* ---------------------------------------------------------------- DOM stub */
const STAGES = ["S1", "ground", "S2", "S3", "S4"];
function element(id = "") {
  const node = {
    id,
    innerHTML: "",
    textContent: "",
    className: "",
    value: "",
    title: "",
    disabled: false,
    style: {},
    dataset: { stage: id },
    classList: {
      _set: new Set(),
      add(name) { this._set.add(name); },
      remove(name) { this._set.delete(name); },
      toggle(name, on) { if (on === undefined) { this._set.has(name) ? this._set.delete(name) : this._set.add(name); } else { on ? this._set.add(name) : this._set.delete(name); } },
      contains(name) { return this._set.has(name); },
    },
    setAttribute() {},
    getAttribute() { return null; },
    addEventListener() {},
    focus() {},
    appendChild() {},
    querySelectorAll() { return []; },
  };
  return node;
}
const nodes = new Map();
const byId = (id) => {
  if (!nodes.has(id)) nodes.set(id, element(id));
  return nodes.get(id);
};
const steps = STAGES.map((stage) => {
  const node = element(stage);
  node.textContent = stage;
  return node;
});

globalThis.document = {
  getElementById: byId,
  createElement: () => element(""),
  querySelectorAll: (selector) => (selector.includes(".progress .step") ? steps : []),
  addEventListener: () => {},
};
globalThis.window = globalThis;
globalThis.fetch = () => Promise.reject(new Error("no network in the render check"));
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

/* ------------------------------------------------------- evaluate the script */
let api;
try {
  api = new Function(
    `${source}\n;return { render, renderAudit, claimCard, evidenceCard, breakdown, stanceBar, showError, paintProgress };`
  )();
} catch (error) {
  console.error(`the inline script failed to evaluate:\n  ${error.message}`);
  process.exit(1);
}
if (!api.render || !api.renderAudit) {
  console.error("render() or renderAudit() is not defined by the inline script");
  process.exit(1);
}

const fixture = process.argv[2] || join(root, "data", "ui_response_fixture.json");
let payload;
try {
  payload = JSON.parse(readFileSync(fixture, "utf8"));
} catch (error) {
  console.error(`cannot read a /api/verify response (default: data/ui_response_fixture.json):\n  ${error.message}`);
  process.exit(1);
}
api.render(payload);

const results = byId("results").innerHTML;
const audit = byId("audit").innerHTML;
const banner = byId("banner").innerHTML;
const claims = payload.report.claims || [];

const failures = [];
const check = (condition, message) => { if (!condition) failures.push(message); };

check(results.includes("claim-text"), "no claim card was rendered");
check((results.match(/class="card claim"/g) || []).length === claims.length,
  `rendered ${(results.match(/class="card claim"/g) || []).length} claim cards, expected ${claims.length}`);
for (const claim of claims) {
  check(results.includes(claim.verdict), `verdict ${claim.verdict} missing from markup`);
  if (claim.evidence.length) {
    check(results.includes("stance s-"), "no stance chip on an evidence card");
    check(/quote verified/.test(results), "no verification-scope label on an evidence card");
  }
  if (claim.claim.article_quote) {
    check(results.includes("Verbatim in your text"), "article quote label missing");
  }
  if (claim.verdict === "INSUFFICIENT EVIDENCE") {
    check(results.includes("Not enough evidence to assess"), "insufficient-evidence explanation missing");
  }
}
for (const key of ["C", "R", "A", "T", "K"]) {
  check(results.includes(`>${key}</td>`), `score component ${key} missing from the breakdown`);
}
check(audit.includes("Audit trail"), "audit footer not rendered");
check(audit.includes("quotes dropped by guard") || audit.includes("quote guard"), "quote-guard counters missing from audit");
check(audit.includes("sources that failed"), "failed sources are not reported in the audit footer");

// The plan's Claim contract lists `entities`. ClaimInput (the frozen scoring schema)
// has no such field on purpose, so the pipeline carries them in `grounding.claims` and
// the UI must join them back onto the card — otherwise the contract field silently
// vanishes between the API and the screen.
const ground = (payload.grounding && payload.grounding.claims) || [];
const withEntities = ground.filter((item) => (item.entities || []).length);
if (withEntities.length) {
  const name = withEntities[0].entities[0];
  check(results.includes(name), `entities from the grounding audit ("${name}") not shown on the claim card`);
}
check(banner.length > 0, "no status banner rendered");
check(!/undefined|NaN|\[object Object\]/.test(results), "markup contains undefined/NaN/[object Object]");

/* ------------------------------------- second pass: the fields that are joined back
   A captured fixture only proves something if it contains it. This payload is written
   by hand to carry exactly the two things the pipeline puts *outside* the claim object
   — S1 entities (ClaimInput has no entities field) and the names of sources that failed
   — so the join-back logic cannot rot into an unchecked no-op. */
const crafted = {
  mode: "offline",
  demo_backup: false,
  note: "",
  s1: { provider: "mock" },
  warnings: ["S2: tavily timed out after 8s"],
  stages: [{ name: "S2 retrieval (offline)", status: "degraded", ms: 3, detail: "1 source failed" }],
  retrieval_summary: { evidence_items: 1, distinct_domains: 1, quote_verified_items: 0, claims_without_evidence: 0, snippet_only_items: 1 },
  s3_audit: { analyzer: "lexical", evidence_analyzed: 1, decisive: 0, neutral: 1, quote_dropped_count: 1 },
  // entity names deliberately do not occur in the claim text: an assertion that leans on
  // shared words passes even when the chip is never rendered.
  grounding: { claims_dropped_ungrounded: 0, claims: [{ id: "claim_001", entities: ["Acme Pte Ltd", "New Delhi NCR"], original_text: "Acme opened a plant in New Delhi." }] },
  per_claim_retrieval: [{
    claim_id: "claim_001",
    sources: [{ id: "ev_1", quote_verified: false, quote: "", snippet: "…", url: "https://example.com/a", domain: "example.com", title: "A", source_kind: "mock", relevance: 0.4, credibility: 0.1, stance: "NEUTRAL", published_at: null }],
    retrieval: { sources_queried: 2, sources_ok: 1, failed_source_names: ["tavily"] },
  }],
  report: {
    disclaimer: "Evidence-weighted assessment.",
    claims: [{
      claim: { id: "claim_001", text: "Acme opened a plant in New Delhi.", type: "factual", article_quote: "Acme opened a plant in New Delhi.", time_sensitive: false },
      verdict: "INSUFFICIENT EVIDENCE", stance_score: 0, evidence_strength: 0.5, score_raw: 0.5, shrinkage_factor: 0,
      scores: { C: 0.35, R: 0, A: 0, T: 0.5, K: 0.5 },
      contributions: { C: 0.105, R: 0, A: 0, T: 0.05, K: 0.05 },
      conflicts: [], evidence: [],
      audit: { n_retrieved: 1, n_used: 0, n_independent: 0, hallucinated_quotes_dropped: 1, unknown_freshness: false, sources_failed: ["tavily"] },
    }],
  },
};
api.render(crafted);
const craftResults = byId("results").innerHTML;
const craftAudit = byId("audit").innerHTML;
const entityChip = (craftResults.match(/title="Entities S1 found[^"]*"[^>]*>([^<]*)/) || [])[1] || "";
check(entityChip.includes("Acme Pte Ltd") && entityChip.includes("New Delhi NCR"),
  `S1 entities are not joined onto the claim card (chip reads: "${entityChip.trim()}")`);

// "sources that failed" must name the source inside its own counter, not merely exist
// somewhere on the page — the warnings line mentions the same source by design.
const failedCell = (craftAudit.match(/sources that failed<\/span><span class="v">([^<]*)/) || [])[1] || "";
check(/tavily/.test(failedCell) && /1/.test(failedCell),
  `failed source is not reported in the audit counter (reads: "${failedCell.trim()}")`);
check(craftAudit.includes("1 / 2") || craftAudit.includes("1/2"),
  "sources queried is not reported as ok/queried");
check(!/undefined|NaN|\[object Object\]/.test(craftResults + craftAudit),
  "crafted payload renders undefined/NaN/[object Object]");

/* ------------------------------------------------- DOM contract of the page */
// Every id the script reaches for must exist in the static markup, or the app throws
// on the first click in a real browser while this file still "passes".
const referenced = new Set();
for (const match of source.matchAll(/\$\(\s*"([a-zA-Z][\w-]*)"/g)) referenced.add(match[1]);
for (const match of source.matchAll(/getElementById\(\s*"([\w-]+)"\s*\)/g)) referenced.add(match[1]);
const declared = new Set([...html.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]));
const missingIds = [...referenced].filter((id) => !declared.has(id));
check(missingIds.length === 0, `script reaches for ids with no element in the markup: ${missingIds.join(", ")}`);

// Tag balance for the *static* page (template strings inside the script are checked by
// rendering them below): an unclosed <div> here breaks the layout for everyone.
const markup = html.slice(0, html.indexOf("<script>")).replace(/<!--[\s\S]*?-->/g, "");
for (const tag of ["div", "section", "main", "header"]) {
  const open = (markup.match(new RegExp(`<${tag}[\\s>]`, "g")) || []).length;
  const close = (markup.match(new RegExp(`</${tag}>`, "g")) || []).length;
  check(open === close, `static <${tag}> is unbalanced: ${open} opening, ${close} closing`);
}
check(!html.includes("<script src=") && !html.includes('rel="stylesheet"'),
  "the page must stay dependency-free: no external <script src> or <link rel=stylesheet>");
check(html.includes("INSUFFICIENT EVIDENCE"), "the insufficient-evidence state is not represented in the UI");
// The plan forbids a big TRUE/FALSE badge. Prose that *explains* the rule is fine;
// what must not exist is an element whose own text is TRUE or FALSE.
const badgeMarkup = [...results.matchAll(/class="[^"]*(?:badge|verdict)[^"]*"[^>]*>([^<]*)</g)].map((m) => m[1].trim());
check(!badgeMarkup.some((text) => /^(TRUE|FALSE)$/i.test(text)),
  "a verdict chip reads as a bare TRUE/FALSE badge");

/* error path must be reachable and must not throw */
api.showError("deliberate failure for the check");
check(byId("error").innerHTML.includes("deliberate failure"), "error state did not render");
check(results.length > 0, "results were cleared by an unrelated error (state leak)");

if (failures.length) {
  console.error(`UI render check FAILED (${failures.length}):\n  - ` + failures.join("\n  - "));
  process.exit(1);
}
console.log(`UI render check passed: ${claims.length} claim cards, ` +
  `${(results.match(/class="ev"/g) || []).length} evidence cards, audit footer and banner present.`);
