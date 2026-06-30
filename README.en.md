<div align="center">

# 🔭 Aria Review

### A Trustworthy Literature-Review Agent Workbench

**Every sentence the AI writes can be traced back to real source evidence.**

![License](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![R](https://img.shields.io/badge/R-4.3+-276DC3?logo=r&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-779%20passing-brightgreen)
![Demo](https://img.shields.io/badge/demo-zero--key-success)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-ff69b4)

**English** · [简体中文](./README.md) · [Quick Start](#-quick-start) · [Architecture](#%EF%B8%8F-architecture) · [Roadmap](#%EF%B8%8F-roadmap)

</div>

---

> ### Other agents hand you an answer. Aria hands you a *verifiable research trail*.

<p align="center">
  <img src="assets/review-provenance.png" width="90%" alt="Traceable review: click a citation and the right pane locates and highlights the source Markdown block, tagged with page and table number">
</p>

<div align="center"><sub>Click citation <code>[1]</code> in the review → the right pane jumps to that exact source block, down to the page and table. Turning "trust me" into "verify it yourself."</sub></div>

**Aria Review** (engineering codename: BiblioCN) is built for researchers. It folds **search → full-text parsing → bibliometrics → AI review → citation checking → source provenance → report export** into a single reproducible system, with one design goal:

**Every claim in a review can be traced back to a real document and checked by you** — and the entire demo runs end-to-end with zero external API keys.

---

## 💡 Why Aria

Today's AI-generated reviews die on three things:

| Symptom | Consequence |
|---|---|
| **Fabricated citations** — citing a paper that doesn't exist in the corpus | One fake citation, and the whole review is untrustworthy |
| **Numbers as decoration** — copying figures into prose without binding them to a source | Data can't be re-checked, so it's worthless |
| **Listing over arguing** — piling up "who did what" instead of "what's still missing" | The reader gets no research judgment |

All three share one root cause: **they cannot be verified.**

And the most expensive judgment in research — *"is this direction genuinely valuable, and has nobody done it yet?"* — is built on top of the review. Once the review is untrustworthy, everything downstream (gap-finding, value assessment) collapses.

**Aria's answer is not "looks more correct" — it's "you can verify it yourself":** the reading strategy is not naive RAG, citations are deterministically re-checked, and every run is logged and can be **recomputed offline.**

---

## 🔁 The Idea: A Verifiable Research Acceleration Loop

```
        ┌───────────────── ④ feeds its verdict back into ① ────────────────┐
        │                                                                  │
        ▼                                                                  │
   ① Multi-source  ──▶  ② Trustworthy  ──▶  ③ Find research ──▶  ④ Validate
      search             review               gaps                research value
   OpenAlex            ★ THE CORE ★          derived from          novelty · feasibility
   Sciverse        per-sentence provenance   the evidence          · checkable
                      · zero fabrication       matrix
```

The trust of the entire chain **rests on link ②**: only if the review itself is verifiable do the downstream "gap" and "value" judgments stand. So Aria pours most of its engineering into **making ② genuinely trustworthy.**

This is what we mean by **AI for Science**: let AI take part in the whole research process — where *every step can be verified.*

---

## 🛡️ How It Earns Trust: Turning "Trust Me" into "Verify It Yourself"

### 1. Structured Parsing ≠ RAG

Writing a review that can be checked needs more than top-k similar snippets. Aria does **full-text structured parsing** of every included paper instead of chunk-and-embed:

| Dimension | Naive RAG | Aria's reading strategy |
|---|---|---|
| Unit | Text chunks + vector embeddings | MinerU splits the full text into paragraph / table / figure / formula blocks |
| Result tables | May be sliced apart | **Whole table preserved** |
| Evidence anchor | Approximate top-k similarity | Every extracted value carries a **page / block anchor** — click back to the source |
| Good at | Quickly locating snippets | The **factual foundation** of the review |
| Role in Aria | Still used for fast evidence discovery | The review's factual base sits on structured parsing |

The agent reads **block by block** — abstract → "findings", methods → "method", result table → "data" — each tagged with the exact page and table. One paper fully read becomes one row in a traceable evidence matrix.

### 2. Three Pieces of Deterministic Engineering

Trust isn't left to the model's "good faith" — it's enforced by deterministic, pure-code checks:

| Engineering | What it does | What you can verify |
|---|---|---|
| **`cite_check`** | Re-checks every citation in the review, in pure code | Inject one fake citation → **deterministic red flag, check FAILS** |
| **Evidence hashing** | Binds every extracted number to its source document | Data traces back to the source and can't be silently altered |
| **RunLog hash chain** | Records the event chain, tool calls, evidence references, and final output | **Recompute the entire run offline** |

> Reading and extraction go to the AI; re-checking and provenance go to code — **write/verify separation.**

### 3. Per-sentence Provenance

Click citation `[4]` in the review, and the right pane locates and highlights the original Markdown of that paper — down to the exact page and table. Readers verify it themselves with one click.

---

## ✨ Core Capabilities

| Capability | Description |
|---|---|
| 🔑 **Zero-key one-command demo** | `docker compose run --rm demo` uses built-in sample corpus + a deterministic LLM to produce a checkable RunLog — no external API key required |
| 🔗 **Traceable reviews** | Citations / data in the review click back to the source Markdown paragraph, table, or structure block |
| 🚫 **Zero-fabrication constraint** | GuardedStream + citation checking flag fabricated citations instead of dressing them up as trustworthy output |
| 📜 **Verifiable run log** | `runlog/v1` hash chain records events, tool calls, evidence references, and final output — independently checkable offline |
| 📈 **Bibliometrics** | R plumber + bibliometrix: sources, authors, keywords, collaboration networks, PRISMA |
| 🧭 **Research co-pilot** | Gap discovery, value validation, evidence packs, and a human-in-the-loop confirmation, adjudicated by a deterministic resolver |
| 🔍 **Multi-source search** | OpenAlex + Sciverse routing, topic search, candidate self-filtering, normalized-key dedup ingestion |
| 🐳 **Three-service deployment** | React frontend + FastAPI agent + R analysis + Postgres, orchestrated by Docker Compose |

---

## 📊 By the Numbers

> Measured on a real systematic-review case, with denominators counted over actual document objects.

- ✅ **779** offline tests green (excluding live real-LLM calls)
- ✅ **130** candidates deduped and ingested in one pass, zero skipped
- ✅ **23** source-provenance anchors with page / block, produced in a single run
- ✅ **0** fabricated citations let through

---

## 📸 Screenshots

<table>
<tr>
<td width="50%" valign="top">
<img src="assets/bibliometric-overview.png" alt="Bibliometric analysis"><br>
<sub><b>📈 Bibliometrics</b><br>Field overview, yearly output, and an author → keyword → source three-field Sankey, powered by bibliometrix.</sub>
</td>
<td width="50%" valign="top">
<img src="assets/ai-review.png" alt="AI review"><br>
<sub><b>🧠 Trustworthy AI review</b><br>A structured review with grounded claims and checked, traceable citations.</sub>
</td>
</tr>
</table>

---

## ⚡ Quick Start

Prerequisites: Docker + Docker Compose v2.

```bash
git clone https://github.com/niuniu-869/aria-review.git
cd aria-review

# Zero-key one-command demo: offline sample corpus + deterministic LLM, checkable RunLog
docker compose run --rm --build demo
```

Expected result on a fresh, zero-key container: RunLog passes 7 checks, final verdict `PASS`, with grounding / zero-fabrication metrics emitted.

Bring up the workbench:

```bash
docker compose up -d --build
curl http://localhost:8000/healthz        # agent health check
# open http://localhost:8080               # frontend workbench
```

The default brings up `web + agent + postgres`. For **upload parsing and bibliometric graphs**, start the heavier R service (first build from source may take 20+ minutes):

```bash
docker compose --profile analysis up -d --build
```

> Postgres is exposed only to `127.0.0.1:55432` on the host to avoid clashing with a local `5432`. To change it, set `POSTGRES_PORT=55433` in the root `.env`.

---

## 🏗️ Architecture

```
                       Browser  ·  apps/web (React + Vite + TypeScript)
                                      │  REST / SSE
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  services/agent  ·  FastAPI                                │
        │  Agent loop · RunLog hash chain · citation check · search │
        └───────────────┬────────────────────────────┬──────────────┘
                        │                            │ projects · papers
        structured parse│                            │ attachments · runs · gaps
        / bibliometrix  ▼                            ▼
                ┌───────────────────┐        ┌──────────────────┐
                │ services/         │        │   PostgreSQL     │
                │ r-analysis        │        └──────────────────┘
                │ R + plumber +     │
                │ bibliometrix      │
                └───────────────────┘
```

| Path | Contents |
|---|---|
| `apps/web` | Frontend workbench, Playwright E2E, Vitest unit tests |
| `services/agent` | FastAPI backend, agent tools, review & safety checks, migrations, pytest |
| `services/r-analysis` | R analysis service, OpenAlex integration, bibliometrix |
| `packages/contracts` | OpenAPI contract — the single source of truth for frontend/backend types |
| `legacy-shiny` | Historical Shiny version snapshot |

> The repo **does not commit** local demo materials, verification screenshots, run outputs, benchmark results, demo corpora, or test data — all kept local via `.gitignore`. The offline demo uses a script's built-in sample corpus, depending on no committed data files.

---

## 🧑‍💻 Local Development

| Tool | Suggested version |
|---|---|
| Docker Compose | v2+ |
| Node.js / pnpm | Node 20+ / pnpm 9+ |
| Python | 3.11+ |
| R | 4.3+ (only for the R analysis service tests) |

**Frontend:**

```bash
pnpm -C apps/web install
pnpm -C apps/web dev          # http://localhost:5173
```

**Agent (start Postgres first):**

```bash
docker compose up -d postgres
cd services/agent
python3 -m pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn
export R_ANALYSIS_URL=http://localhost:8001
python scripts/wait_for_db.py --timeout 60
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**R analysis service:**

```bash
PORT=8001 Rscript -e 'setwd("services/r-analysis"); library(plumber); plumb("plumber.R")$run(host="0.0.0.0", port=8001)'
```

### Optional Environment Variables

```bash
cp .env.example .env
```

**Every key is optional.** When unset, the system takes an offline or deterministic fallback path and still runs the demo. A user's LLM / Sciverse / Image keys are passed through request headers — **never written to the database, never echoed back.**

| Variable | Purpose | When unset |
|---|---|---|
| `OCR_AUTHORIZATION_TOKEN` | MinerU real full-text parsing | demo uses built-in Markdown; real upload parsing degrades |
| `DEEPSEEK_API_KEY` | Real LLM review & AI tools | FakeLLM / deterministic fallback |
| `SCIVERSE_API_TOKEN` | Sciverse metadata & full-text search | Sciverse path unavailable; OpenAlex still works |
| `IMAGE_API_KEY` | Infographic generation | SVG fallback or prompt-only |
| `CORS_ORIGINS` | Allowed frontend origins for the agent | Docker default restricts to `http://localhost:8080` |
| `POSTGRES_PORT` | Host port for Compose Postgres | `55432` |

---

## 🔬 Verification

Before submitting, run at least:

```bash
docker compose config -q
pnpm -C apps/web test
pnpm -C apps/web build
cd services/agent && \
  DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn \
  TEST_DATABASE_URL=postgresql+asyncpg://bibliocn:bibliocn@localhost:55432/bibliocn_test \
  python3 -m pytest -q
```

R-side tests:

```bash
Rscript -e 'testthat::test_dir("services/r-analysis/tests/testthat")'
```

Independently verify the demo RunLog — this is the "verify it yourself" entry point:

```bash
docker compose run --rm agent python scripts/verify_runlog.py \
  /data/corpora/demo_runlog.json \
  --corpus-hashes /data/corpora/demo_corpus_hashes.json
```

---

## 🔒 Security & Privacy

- A user's LLM / Sciverse / Image keys are passed through request headers — **never written to the database, never echoed.**
- `.env` and backup env files are ignored by default; example files contain only placeholders. **Never commit** `.env`, API keys, database URLs with passwords, private keys, or copied request headers.
- Review output never trusts the LLM directly; citations go through citation / grounding checks.
- The RunLog hash chain proves event **self-consistency**, not tamper-proof notarization. For stronger integrity, anchor `chain_head` to external immutable storage or a signing system.

Report security issues **privately** per [SECURITY.md](SECURITY.md) — do not open a public issue.

---

## 🗺️ Roadmap

| Stage | Status |
|---|---|
| ② Trustworthy review core (provenance / zero-fabrication / RunLog hash chain) | ✅ Shipped · the core of this project |
| ① Multi-source search (OpenAlex + Sciverse normalized dedup ingestion) | ✅ Shipped |
| ③ Find research gaps (review has built-in "disagreements & gaps" / "future directions", each derived from the evidence matrix and traceable) | ✅ Built in |
| ④ Validate research value (review has a built-in "research gap & value of this study" argument section) | ✅ Built in |
| Deeper **automated novelty checking** (turning ④'s re-check into a standalone verifiable module) | 🚧 Next |
| Core portability (domain-agnostic: intelligence analysis, spec parsing, corpus QA) | 🔭 Exploring |

> We're honest about the boundary: ③④ are built in as traceable review sections; deeper **automated novelty checking** is the next station we're pushing toward — no overclaiming. "Verifiable" precisely means three things — the citation exists, the evidence traces, and the log recomputes offline; semantic correctness is backstopped by a human in the loop, and we never claim to "prove the conclusion is right."

---

## 🤝 Contributing

We welcome improvements. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first: keep changes scoped, prefer existing patterns, update `packages/contracts/openapi.yaml` and the generated types together when the API shape changes, and **never commit `.env` or API keys.**

## 📚 Docs

- [Monorepo notes](MONOREPO.md) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [License](LICENSE)

## 📄 License

[MIT License](LICENSE).

---

<div align="center">

**Let AI take part in the whole research process — where every step can be verified.**

<sub>Built for researchers who need to trust what the AI wrote.</sub>

</div>
