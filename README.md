# AI-Powered Enterprise Workflow Copilot (Simulated)

A request-resolution copilot for operational requests. It combines **RAG over SOPs** with CRM / ticket
context to classify a request, plan the next steps, and execute them as **tool calls against simulated
Jira, CRM and Slack**, with permission checks, **human approval for sensitive changes**, retries and a
tamper-evident **audit log**. Everything runs locally with free tools: no API key needed.

```
                     POST /requests
                          │
   ┌──────────────────────▼───────────────────────┐
   │ 1 context   directory · CRM customer/order · Jira   (read tools, all audited)
   │ 2 retrieve  TF-IDF (word + char n-grams) over 12 SOPs  → intent scores + cited chunks
   │ 3 plan      Planner proposes tool calls  ── local (default) | Anthropic | OpenAI-compatible/Ollama
   │ 4 policy    role gate · target scope · tool whitelist per SOP      ← enforced HERE, not by the planner
   │ 5 execute   step by step; {ticket_id} etc. flow between steps; idempotent writes; retry + backoff
   │             sensitive step → status=awaiting_approval (state saved in SQLite)
   │ 6 outcome   completed | awaiting_approval | denied | rejected | escalated (triage ticket + #ops-triage)
   └──────────────────────┬───────────────────────┘
        POST /approvals/{id}/decision   (separation of duties, role + line-manager checks)
                          │
        SQLite: requests · approvals · hash-chained audit_log        GET /metrics  /audit  /audit/verify
```

## Quick start

```bash
pip install -r requirements.txt
python scripts/demo.py                  # narrated demo of 3 flows
python -m pytest -q                     # 56 tests
python scripts/run_eval.py              # 200-case evaluation → eval_results/report.md
uvicorn app.main:app --reload           # API docs at http://127.0.0.1:8000/docs
```

```bash
curl -s localhost:8000/requests -H 'content-type: application/json' \
  -d '{"requester_id":"U023","text":"Please grant me admin access to the data warehouse"}'
# → status "awaiting_approval", approval_id "APR-…"
curl -s localhost:8000/approvals/APR-…/decision -H 'content-type: application/json' \
  -d '{"approver_id":"U001","decision":"approve","reason":"needed for Q4"}'
```
Users and customers are in `data/users.json` / `data/crm.json` (regenerate with `python scripts/generate_data.py`).

## Choosing the planner (`COPILOT_PLANNER`, see `.env.example`)

| Value | What it does | Needs |
|---|---|---|
| `local` (default) | RAG picks the SOP; the SOP's action template becomes the plan. Deterministic, offline. | nothing |
| `openai_compat` | Real LLM tool-calling via any OpenAI-compatible server, e.g. **Ollama** (`llama3.1`) | local server |
| `anthropic` | Real LLM tool-calling via the Anthropic Messages API | `ANTHROPIC_API_KEY` |

LLM planners fall back to the local planner on any error. Whatever the planner proposes, `agent.py` re-checks it:
it cannot call tools outside the matched SOP, cannot skip approval for sensitive tools, and cannot approve its own request
(all covered by tests). The LLM planners are tested against stubbed HTTP transports; they have **not** been run
against a live model in this repo, so try one and check `python scripts/run_eval.py` before quoting LLM numbers.

## Design choices worth knowing
* **SOP front matter is the contract**: allowed roles, approver roles, sensitive tools, required fields and the action list live in `data/sops/*.md`. Add a workflow by adding a file.
* **Idempotent writes**: every write carries `request_id:step` as an idempotency key, so a retry never creates a second ticket or refund.
* **Durable approvals**: run state is stored in SQLite, so an approval can arrive after a restart (tested).
* **Least privilege by default**: an access request that names no level gets `viewer`.
* **Audit log is hash-chained**: each row hashes the previous one; `GET /audit/verify` detects edits or deletions.
* **Failure taxonomy** (tracked in `/metrics`): `low_confidence`, `missing_information`, `permission_denied`, `approval_rejected`, `tool_error`, `service_unavailable`, `policy_violation`, `internal_error`.

## What is real vs simulated
| Real | Simulated |
|---|---|
| FastAPI service, RAG retrieval, routing, policy engine, approvals, retries, idempotency, SQLite audit trail | Jira, CRM, Slack (in-memory mocks in `app/mocks.py`) |
| Measured compute latency | Per-call API latency (virtual: Jira 350 ms, CRM 250 ms, Slack 150 ms) and the *manual* baseline (assumed step times in `app/timing.py`) |

## Evaluation (`python scripts/run_eval.py`)
200 synthetic requests = 12 intents × 15 + 20 out-of-scope, with noise (typos, filler, lowercase), denied-role cases,
missing-information cases, over-refund cases and approve/reject decisions. Each case has an expected intent **and**
an expected final outcome. A fixed 80-case *dev* / 120-case *test* split is stored in `data/eval_cases.jsonl`.

Caveats you should state if you quote the numbers: the cases are **synthetic and written by the same author as the SOPs**,
so accuracy is indicative, not a real-world estimate. No thresholds or SOP wording were tuned against the test failures
(the one SOP change, `requires: [amount]` for expenses, is a correctness fix that does not alter routing). The manual
baseline is a **model**, not a stopwatch measurement; edit `app/timing.py` to match what you observe. Human approval
wait time is excluded on both sides.

Results of the committed run are in `eval_results/report.md` (clean) and `report_fail5.md` (5% injected transient failures).
Re-run to regenerate; copy the **measured** figures into your resume instead of round numbers.
