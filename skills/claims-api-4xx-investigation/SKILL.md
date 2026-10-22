---
name: claims-api-4xx-investigation
description: >
  Deterministic investigation procedure for the claims-api service when it
  returns a high rate of HTTP 4xx client errors (especially HTTP 422
  Unprocessable Entity) on POST /claims while the service otherwise looks
  healthy (pods Running, no 5xx, no restarts, normal CPU/memory). Use this
  skill whenever investigating a claims-api error-rate spike, a
  "claims-api-high-4xx-error-rate" CloudWatch alarm, 422 rejections on
  /claims, or reports that claims are not going through on claims-api in the
  "claims" namespace on the devops-agent-eks-dev-cluster EKS cluster. It tells
  you exactly what code to run in the sandbox to quantify the failure, isolate
  the input signature, correlate it to a recent code change, and produce the
  root cause and the precise fix.
---

# claims-api 4xx (HTTP 422) Investigation

Use this skill when `claims-api` (namespace `claims`, EKS cluster
`devops-agent-eks-dev-cluster`, eu-west-1) is rejecting a large share of
`POST /claims` requests with **HTTP 422** while the service is otherwise
healthy. HTTP 422 means the request reached the app and was rejected by
application-level validation — this is a code/validation regression, not an
infrastructure, capacity, or dependency failure. Do not chase CPU, memory,
networking, or the database; the signal is in the application logs and the
recent code change.

**Run the diagnostic code in your sandbox.** The steps below each specify a
concrete piece of Python (boto3 + standard library only — no extra packages
and no outbound internet needed) to write and execute in your sandbox. Run
them in order, read the printed results back into your analysis, and include
the code and its output in the investigation summary.

## Step 1 — Quantify the error rate from CloudWatch Logs

The app emits one structured JSON access-log line per request to the log group
`/aws/eks/devops-agent-eks-dev/claims-api`. Each line has top-level fields:
`ts, method, path, status, latency_ms, ref_len, claim_type, version`.

Run this in the sandbox to measure the current 4xx rate and confirm the
"healthy but rejecting" shape (422s present, 5xx absent):

```python
import boto3, time, json
logs = boto3.client("logs", region_name="eu-west-1")
LG = "/aws/eks/devops-agent-eks-dev/claims-api"
end = int(time.time() * 1000)
start = end - 15 * 60 * 1000  # last 15 minutes

q = logs.start_query(
    logGroupName=LG, startTime=start // 1000, endTime=end // 1000,
    queryString="""
      fields status
      | filter ispresent(status)
      | stats count(*) as n by status
      | sort status asc
    """,
)
qid = q["queryId"]
while True:
    r = logs.get_query_results(queryId=qid)
    if r["status"] in ("Complete", "Failed", "Cancelled"):
        break
    time.sleep(1)
rows = {row[0]["value"]: int(row[1]["value"]) for row in r["results"]}
total = sum(rows.values())
err4xx = sum(v for k, v in rows.items() if k.isdigit() and 400 <= int(k) < 500)
err5xx = sum(v for k, v in rows.items() if k.isdigit() and 500 <= int(k) < 600)
print("status counts:", rows)
print(f"total={total}  4xx={err4xx} ({100*err4xx/total:.1f}%)  5xx={err5xx}")
```

Expected signature: a large 4xx share (status 422), **zero 5xx**. That
confirms a validation regression rather than an outage.

## Step 2 — Isolate the input signature (which requests fail)

Decompose the failures by `claim_type` and by `ref_len` to find exactly which
inputs are being rejected. Run in the sandbox:

```python
# reuse `logs`, `LG`, `start`, `end` from Step 1
def run(query):
    qid = logs.start_query(logGroupName=LG, startTime=start // 1000,
                           endTime=end // 1000, queryString=query)["queryId"]
    while True:
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled"):
            return r["results"]
        time.sleep(1)

print("422 rate by claim_type:")
for row in run("""
  fields status, claim_type
  | filter path = "/claims"
  | stats count(*) as total, sum(status=422) as rejected by claim_type
  | sort claim_type asc
"""):
    d = {c["field"]: c["value"] for c in row}
    t, rej = int(d["total"]), int(d["rejected"])
    print(f"  {d.get('claim_type'):<10} {100*rej/t:5.1f}% rejected  (n={t})")

print("\n422 rate by reference length (top offenders):")
for row in run("""
  fields status, ref_len
  | filter path = "/claims"
  | stats count(*) as total, sum(status=422) as rejected by ref_len
  | sort rejected desc
  | limit 10
"""):
    d = {c["field"]: c["value"] for c in row}
    t, rej = int(d["total"]), int(d["rejected"])
    print(f"  ref_len={d.get('ref_len'):<4} {100*rej/t:5.1f}% rejected  (n={t})")
```

Interpretation: if a specific `claim_type` fails ~100% and/or a specific
`ref_len` boundary fails ~100% while smaller lengths pass, that is the exact
input the new validation logic wrongly rejects — the fingerprint of the
regression.

## Step 3 — Correlate to the most recent code change

A validation regression that appears abruptly almost always maps to a recent
deployment. Confirm the version change from the logs (the `version` field
changes at the incident boundary), then correlate to the GitHub history of the
connected repository `chetansuri10101984-cloud/test2`.

```python
# version timeline from the logs
for row in run("""
  fields version
  | filter ispresent(version)
  | stats count(*) as n, sum(status=422) as rejected by version
  | sort version asc
"""):
    d = {c["field"]: c["value"] for c in row}
    n, rej = int(d["n"]), int(d["rejected"])
    print(f"  version {d.get('version')}: {n} reqs, {rej} rejected ({100*rej/n:.0f}%)")
```

Then review the most recent merged change on `main` of
`chetansuri10101984-cloud/test2` (use the GitHub integration). The change that
immediately precedes the 422 onset is the suspect — look specifically at
`app/validation.py` and `app/query.sql`.

## Step 4 — Pinpoint the defect in the code

Compare the suspect version of `app/validation.py` against the previous
(last-good) version. Focus on two things that produce 422 on valid input:

1. The reference-length guard in `validate_reference` — a boundary comparison
   operator that rejects a previously-valid length (for example `<` where it
   should be `<=` against `MAX_REFERENCE_LEN`).
2. The `claim_type` allowlist — a value dropped from the accepted set (in
   `validate_claim_type` / `_query_allowlist` and the `ANY(ARRAY[...])` literal
   in `app/query.sql`).

Cross-check the two allowlists against the database's own
`claims.claim_type_allowlist` table (the DB is the source of truth): any value
present in the table but missing from the application's allowlist is being
wrongly rejected.

## Step 5 — Root cause and fix

State the root cause as: the most recent claims-api deployment introduced a
request-validation regression in `app/validation.py` (and/or `app/query.sql`)
that rejects valid `POST /claims` payloads with HTTP 422. Back it with the
numbers from Steps 1–3 (4xx rate, the failing `claim_type`/`ref_len`
signature, and the version boundary).

Recommend a precise, minimal fix:
- Restore the reference-length comparison to accept lengths up to and
  including `MAX_REFERENCE_LEN`.
- Restore the dropped value(s) to the `claim_type` allowlist in both
  `app/validation.py` and `app/query.sql` so it matches
  `claims.claim_type_allowlist`.

Deliver the fix as a pull request against `chetansuri10101984-cloud/test2`
targeting the files identified above, so a human can review and merge it.
