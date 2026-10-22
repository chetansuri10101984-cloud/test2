# Autonomous Incident Response — Architecture & Scenario

An internal explainer for the demo built on this repository: a real production
incident on an EKS service that is detected, investigated, root-caused, and
fixed **by an AI agent**, with a human only approving the final merge.

This document describes the scenario, the end-to-end architecture, and exactly
what happens at each step. It is written to be read by a colleague who has not
seen the build.

---

## 1. The scenario in one paragraph

`claims-api` is an insurance claims intake service running on Amazon EKS, with
its code in this GitHub repository. A developer merges a change that *looks*
like a harmless performance optimisation but actually introduces a
request-validation regression: the service starts rejecting a large share of
perfectly valid requests with **HTTP 422**. The pods stay healthy, there are no
5xx errors, CPU and memory look normal — so classic infra monitoring says
"everything is fine," while nearly half of real customer traffic is being
turned away. A CloudWatch alarm on the 422 rate fires and **automatically**
starts an AWS DevOps Agent investigation. The agent spins up its own sandbox,
runs diagnostic code against the logs, isolates the exact failing input
signature, correlates it to the bad commit, and writes a root-cause analysis
and a mitigation plan. That mitigation automatically drives an **automated
GitHub pull request** containing the minimal code fix. A human reviews and
merges the PR, the service is redeployed, and the error rate drops to zero.

The point of the demo: the agent does the investigation and the fix. The human
does the approval.

---

## 2. The bug (what actually breaks)

The "optimisation" commit changes two things in the request-validation path:

| File | Correct (v1.4.2) | Broken (v1.5.0) | Effect |
|------|------------------|-----------------|--------|
| `app/validation.py` | `if not (len(reference) <= MAX_REFERENCE_LEN)` | `if not (len(reference) < MAX_REFERENCE_LEN)` | A reference of **exactly 64** chars (a legitimate, common width) is now rejected |
| `app/validation.py` | `_query_allowlist()` returns `("health","property","auto")` | returns `("health","property")` | Every **`auto`** claim is now rejected |
| `app/query.sql` | `ANY (ARRAY['health','property','auto'])` | `ANY (ARRAY['health','property'])` | The SQL allowlist agrees with the broken app allowlist |

Note the tell that makes the root cause satisfying: the module-level constant
`ALLOWED_CLAIM_TYPES` still correctly lists all three types — the "right
answer" is sitting in the file the whole time; the code just stopped using it.

Against a constant, legitimate traffic mix this produces a steady **~44% HTTP
422 rate** — and **zero** 5xx. That "up but wrong" shape is the whole puzzle:
nothing is down, yet the service is failing customers.

---

## 3. Architecture at a glance

```
                         ┌─────────────────────────────────────────────────────┐
                         │                    Amazon EKS                          │
  developer merges       │   claims-api (v1.5.0, buggy)   ── JSON access logs ──┐ │
  bad PR ───────────────▶│   3 pods, healthy, ~44% 422                          │ │
                         └───────────────────────────────────────────────────┬─┘ │
                                                                              │   │
                                           fluent-bit DaemonSet ──────────────┘   │
                                                                              │
                                                      CloudWatch Logs  ◀──────┘
                                                      /aws/eks/.../claims-api
                                                              │
                                 metric filter { $.status = 422 }
                                                              ▼
                                       CloudWatch metric  ClaimsApi/Claims422Count
                                                              │
                                        alarm: >20 422/min  ──�’ claims-api-high-4xx-error-rate
                                                              ▼
                                                            SNS topic
                                                              ▼
                                           Lambda  devops-agent-eks-dev-devops-trigger
                                      (builds incident payload, HMAC-signs, POSTs)
                                                              ▼
                                        AWS DevOps Agent  — generic webhook
                                                              │
                 ┌────────────────────────────────────────────┤  autonomous
                 ▼                                              │  investigation
        ┌──────────────────┐   guided by imported Skill        │
        │  DevOps Agent     │   "claims-api-4xx-investigation"  │
        │  SANDBOX          │◀──────────────────────────────────┘
        │  (ephemeral,      │   runs boto3 + CloudWatch Logs Insights
        │   agent-owned)    │   -> quantify 422, isolate input signature,
        └──────────────────┘   correlate to commit, pinpoint defect
                 │
                 ▼
         Root cause + (human clicks "Generate mitigation plan") ─▶ Mitigation plan
                 │
                 ▼  EventBridge event:  source=aws.aidevops, detail-type="Mitigation Completed"
        ┌─────────────────────────────────────────────────────────────────────┐
        │  Auto-remediation bridge (this is the "agent writes the fix" part)     │
        │                                                                        │
        │  EventBridge rule ─▶ extract Lambda ─▶ SQS ─▶ trigger Lambda ─▶ CodeBuild
        │  (reads the mitigation       (queue)          (starts build)   │        │
        │   summary via the API)                                         ▼        │
        │                                             headless Kiro CLI writes    │
        │                                             the minimal fix, guarded,   │
        │                                             pushes a branch, opens a    │
        │                                             GitHub Pull Request         │
        └─────────────────────────────────────────────────────────────────────┘
                 │
                 ▼
          Human reviews + merges the PR  ─▶  redeploy (v1.5.1)  ─▶  422 rate → 0%
```

---

## 4. The two halves of the system

The build is really two independent, event-driven pipelines joined by the
DevOps Agent in the middle.

### Half A — Detect & investigate (symptom → root cause)

1. **App logging.** `claims-api` writes one structured JSON line per request
   (`status`, `claim_type`, `ref_len`, `version`, …). A fluent-bit DaemonSet
   ships these to CloudWatch Logs (`/aws/eks/devops-agent-eks-dev/claims-api`).
2. **Metric filter.** `{ $.status = 422 }` turns each 422 log line into the
   CloudWatch metric `ClaimsApi/Claims422Count`.
3. **Alarm.** `claims-api-high-4xx-error-rate` fires when more than 20 422s
   occur in a 60-second window. Its description deliberately carries context
   (service, namespace, "HTTP 422 on POST /claims", "no 5xx") so the agent gets
   a rich starting point.
4. **Auto-trigger.** Alarm → SNS → a Lambda that assembles a generic incident
   payload, HMAC-signs it, and POSTs it to the DevOps Agent **generic webhook**.
   This is the documented "webhooks trigger investigations" path. Crucially, the
   payload states *symptoms*, not a prescription — the agent decides how to
   investigate.
5. **Autonomous investigation + Skill + Sandbox.** The agent picks up the
   incident and matches the imported **Skill**
   `claims-api-4xx-investigation`, which gives it a deterministic 5-step
   procedure. It provisions its **own ephemeral sandbox** and runs diagnostic
   code there (boto3 + CloudWatch Logs Insights) to:
   - quantify the 422 rate and confirm there are no 5xx (→ validation
     regression, not an outage);
   - decompose the 422s by `claim_type` and `ref_len` to find the exact failing
     inputs (100% of `auto` claims; 100% of 64-char references);
   - correlate the error onset to the `version` boundary and the most recent
     merged commit;
   - pinpoint the defect in `app/validation.py` and `app/query.sql`.
   It then writes a root-cause analysis. (The sandbox is created, used, and
   torn down by the agent — nothing external provisions it.)

### Half B — Remediate (root cause → fix → recovery)

6. **Human accepts mitigation.** The investigation *offers* a mitigation plan;
   a human clicks **Generate mitigation plan** (the intended control point).
   When mitigation completes, the agent emits an EventBridge event
   `source = aws.aidevops`, `detail-type = "Mitigation Completed"`.
7. **The bridge.** That event drives an event-driven pipeline (modelled on the
   official `aws-samples/sample-devops-kiro-integration`, adapted from
   CodeCommit to GitHub):
   - **EventBridge rule** (scoped to this agent space + `status = COMPLETED`)
     → **extract Lambda**. The event carries no summary text, so the Lambda
     calls `ListJournalRecords` to fetch the mitigation summary markdown and
     publishes it to **SQS**.
   - **trigger Lambda** consumes the queue and starts a **CodeBuild** job,
     passing the mitigation payload as an environment variable.
   - **CodeBuild** installs **Kiro CLI in headless mode**, converts the
     mitigation into a prompt, and runs the agent with `--no-interactive`.
     Guided by the repo's remediation **steering file**, Kiro edits only
     `app/validation.py` and `app/query.sql` with the minimal fix.
   - **Guardrails** then validate the diff (at most 2 files, only those 2 files,
     Python syntax check) and abort the build on any violation.
   - CodeBuild pushes a branch and opens a **GitHub pull request**.
8. **Human merges, service recovers.** A developer reviews the PR (a tiny,
   obvious 2-file diff), merges it, and the fixed image (v1.5.1) is deployed.
   The same traffic mix now returns 100% success and the alarm clears.

---

## 5. Why it's built this way (design notes)

- **The agent is never told the answer.** The alarm payload describes symptoms.
  The Skill describes a *method*, not the fix. The agent reaches the root cause
  itself. That's what makes it a real demonstration rather than a scripted one.
- **The sandbox is the agent's, not ours.** All diagnostic code runs inside the
  DevOps Agent's own ephemeral sandbox, which it provisions and discards per
  investigation. The Skill's code uses only boto3 + the Python standard library
  + AWS endpoints, so it needs no extra packages and no outbound internet — it
  runs in the sandbox with zero additional configuration.
- **The fix is written by an agent, with guardrails.** Headless Kiro CLI writes
  the actual diff, but a steering file constrains it to the two validation files
  and a buildspec guardrail enforces that constraint before any PR is opened.
  This keeps the "AI wrote the fix" story true while making it safe to run
  unattended.
- **A human stays in the loop at two points:** accepting the mitigation plan,
  and merging the pull request. Nothing reaches production without a person.
- **Everything is event-driven.** No polling, no cron. Each stage reacts to an
  event from the previous one (log line → metric → alarm → SNS → webhook →
  investigation → mitigation event → EventBridge → SQS → CodeBuild → PR).

---

## 6. Components and where they live

| Component | Where | Purpose |
|-----------|-------|---------|
| `claims-api` app | `app/` (this repo) | The service that breaks and gets fixed |
| Kubernetes manifests | `deploy/k8s/` | Namespace, Deployment, Service on EKS |
| Investigation Skill | `skills/claims-api-4xx-investigation/SKILL.md` | The method the agent follows; imported into the agent space |
| Remediation steering | `.kiro/steering/remediation-agent.md` | Constrains headless Kiro to the minimal, safe fix |
| CodeBuild buildspec | `buildspec.yml` | Installs Kiro, runs it headless, runs guardrails, opens the PR |
| Prompt generator | `bridge/prompt_generator.py` | Turns the mitigation payload into a Kiro prompt |
| Guardrails | `bridge/guardrails.sh` | Enforces "≤2 files, only the 2 validation files, syntax-valid" |
| PR opener | `bridge/open_pr.sh` | Branch + commit + push + GitHub PR via the REST API |
| Bridge infrastructure | *(ops repo)* `bridge/remediation-bridge.yaml` | EventBridge rule, extract/trigger Lambdas, SQS, DLQ, CodeBuild project |
| Trigger Lambda | *(ops repo)* `devops-agent-eks-dev-devops-trigger` | Alarm → signed incident → DevOps Agent webhook |

Runtime platform: Amazon EKS (Graviton/arm64 nodes), Amazon RDS PostgreSQL,
Amazon ECR, CloudWatch (Logs, metric filter, alarm), SNS, Lambda, EventBridge,
SQS, CodeBuild — all in `eu-west-1`.

---

## 7. The end-to-end flow, as a numbered timeline

1. Developer merges the "perf optimisation" PR → `main` now contains the bug.
2. The buggy image is deployed to EKS. Pods are healthy; ~44% of requests get
   HTTP 422; no 5xx.
3. 422 log lines → metric filter → `Claims422Count` climbs.
4. The alarm crosses its threshold and goes **ALARM** (well under a minute).
5. SNS → trigger Lambda → DevOps Agent webhook returns `200 Webhook received`.
6. The agent opens a **HIGH** investigation automatically.
7. It loads the Skill, provisions its sandbox, runs diagnostic code, and
   produces the root cause (bad commit, two validation defects).
8. A human clicks **Generate mitigation plan**; mitigation completes and emits
   the **Mitigation Completed** event.
9. The bridge runs: EventBridge → extract Lambda → SQS → trigger Lambda →
   CodeBuild → headless Kiro writes the fix → guardrails pass → **GitHub PR
   opened**.
10. A human reviews and merges the PR.
11. The fixed image (v1.5.1) is deployed; the same traffic now returns 100%
    success; the alarm returns to **OK**.

That is the whole loop: **alarm → autonomous investigation (sandboxed) →
root cause → mitigation → automated pull request → human merge → recovery.**
