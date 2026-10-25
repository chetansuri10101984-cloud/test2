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
  "claims" namespace on the devops-agent-eks-dev-cluster EKS cluster. It gives
  you a ready-made Python diagnostic to run in the sandbox that quantifies the
  failure, isolates the failing input signature, and finds the version
  boundary, then tells you how to confirm the cause in the code.
---

# claims-api 4xx (HTTP 422) Investigation

Use this skill when `claims-api` (namespace `claims`, EKS cluster
`devops-agent-eks-dev-cluster`, account `725425002230`, region `eu-west-1`) is
rejecting a large share of `POST /claims` requests with **HTTP 422** while the
service is otherwise healthy. HTTP 422 means the request reached the app and
was rejected by application-level validation. Treat it as a code/validation
regression, not an infrastructure, capacity, or dependency failure. The
evidence is in the application logs and the most recent code change.

## Sandbox environment (read this first)

The diagnostic below is written for the sandbox as it actually is. Follow these
rules so nothing fails:

- **Use Python 3 with the standard library only** (`json`, `subprocess`,
  `sys`, `time`). The sandbox runs Python 3.9.
- **Do not `import boto3` and do not `pip install` anything.** boto3 is not
  installed, and the sandbox has no internet access, so installs fail.
- **Use the AWS CLI for all AWS data.** It is installed and has read-only
  credentials. Set `AWS_PROFILE=725425002230` on every command.
- **Do not use `which`**; it is not installed. Use `command -v` if you need it.
- Write code to a file with a quoted heredoc (`<< 'CLAIMS_DIAG_EOF'`) rather
  than inline `python3 -c '...'`, to avoid shell-quoting errors.

The application writes one JSON line per request to the log group
`/aws/eks/devops-agent-eks-dev/claims-api`, with top-level fields
`ts, method, path, status, latency_ms, ref_len, claim_type, version`.

## Step 0 - Write the diagnostic into the sandbox

Run this once, exactly as written. It creates `/tmp/claims_diag.py`, a small
standard-library Python program that runs CloudWatch Logs Insights queries
through the AWS CLI and prints a table plus a one-line VERDICT for each step.

```bash
cat > /tmp/claims_diag.py << 'CLAIMS_DIAG_EOF'
"""claims-api 422 diagnostics for the DevOps Agent sandbox.

Python 3.9, standard library only (no boto3, no pip). Calls the AWS CLI,
which is available in the sandbox with read-only credentials.

Usage:
  AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py quantify [minutes]
  AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py isolate  [minutes]
  AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py timeline [minutes]
"""
import json
import subprocess
import sys
import time

REGION = "eu-west-1"
LOG_GROUP = "/aws/eks/devops-agent-eks-dev/claims-api"


def aws(*args):
    """Run an AWS CLI command and return its parsed JSON output."""
    cmd = ["aws", "--region", REGION, "--output", "json"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise SystemExit("aws %s failed: %s" % (args[1], p.stderr.strip()[:300]))
    return json.loads(p.stdout or "{}")


def insights(query, minutes):
    """Run a CloudWatch Logs Insights query and return rows as dicts."""
    end = int(time.time())
    qid = aws("logs", "start-query", "--log-group-name", LOG_GROUP,
              "--start-time", str(end - minutes * 60), "--end-time", str(end),
              "--query-string", query)["queryId"]
    for _ in range(60):
        r = aws("logs", "get-query-results", "--query-id", qid)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(1)
    if r["status"] != "Complete":
        raise SystemExit("query ended with status " + r["status"])
    return [{c["field"]: c["value"] for c in row} for row in r["results"]]


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def quantify(minutes=30):
    """Step 1: how many requests fail, and is it 4xx (bad input) or 5xx (outage)?"""
    rows = insights("fields status | filter ispresent(status) "
                    "| stats count(*) as n by status | sort status asc", minutes)
    counts = {r["status"]: int(r["n"]) for r in rows}
    total = sum(counts.values())
    e4 = sum(v for k, v in counts.items() if k.isdigit() and 400 <= int(k) < 500)
    e5 = sum(v for k, v in counts.items() if k.isdigit() and 500 <= int(k) < 600)
    print("STEP 1 - QUANTIFY (last %d min)" % minutes)
    for k in sorted(counts):
        print("  status %-4s %7d" % (k, counts[k]))
    print("  total=%d  4xx=%d (%.1f%%)  5xx=%d" % (total, e4, pct(e4, total), e5))
    if total and e5 == 0 and e4:
        print("VERDICT: service is up but rejecting input (4xx, zero 5xx) -> validation regression, not an outage")
    elif e5:
        print("VERDICT: 5xx present -> server-side failure, look beyond validation")
    else:
        print("VERDICT: no errors in this window")


def isolate(minutes=30):
    """Step 2: which inputs are rejected? Look for cohorts rejected 100% of the time."""
    # Look only at the version producing the rejections, so healthy versions don't dilute the numbers.
    worst = insights("fields version | filter status = 422 | stats count(*) as n by version "
                     "| sort n desc | limit 1", minutes)
    if not worst:
        print("STEP 2 - ISOLATE: no 422s in the last %d min" % minutes)
        return
    ver = worst[0]["version"]
    print("STEP 2 - ISOLATE (last %d min, version %s)" % (minutes, ver))
    for dim in ("claim_type", "ref_len"):
        rows = insights('fields status, %s | filter path = "/claims" and version = "%s" '
                        "| stats count(*) as total, sum(status=422) as rejected by %s "
                        "| sort rejected desc | limit 8" % (dim, ver, dim), minutes)
        print("  by %s:" % dim)
        for r in rows:
            t, j = int(r["total"]), int(float(r["rejected"]))
            flag = "   <-- 100% rejected" if t and j == t else ""
            print("    %-10s total=%6d rejected=%6d  %5.1f%%%s" % (r.get(dim, "?"), t, j, pct(j, t), flag))
    print("VERDICT: any value rejected 100% is the input fingerprint of the regression")


def timeline(minutes=360):
    """Step 3: which version introduced it, and when did the 422s start?"""
    print("STEP 3 - TIMELINE (last %d min)" % minutes)
    rows = insights("fields version | filter ispresent(version) "
                    "| stats count(*) as n, sum(status=422) as rejected, "
                    "earliest(ts) as first_seen, latest(ts) as last_seen by version "
                    "| sort first_seen asc", minutes)
    for r in rows:
        n, j = int(r["n"]), int(float(r["rejected"]))
        print("  v%-7s requests=%6d rejected=%6d (%5.1f%%)  first=%s  last=%s"
              % (r["version"], n, j, pct(j, n), r.get("first_seen"), r.get("last_seen")))
    bad = [r for r in rows if int(float(r["rejected"]))]
    # last good = the clean version that was already running before the first failing one appeared
    good = [r for r in rows if not int(float(r["rejected"]))
            and bad and r.get("first_seen", "") < bad[0].get("first_seen", "")]
    if bad and good:
        print("VERDICT: v%s had no rejections; rejections start with v%s (first seen %s)"
              % (good[-1]["version"], bad[0]["version"], bad[0].get("first_seen")))
    elif bad:
        print("VERDICT: all traffic in this window is on a failing version; widen the window to find the last good one")
    else:
        print("VERDICT: no rejections in this window")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = [int(sys.argv[2])] if len(sys.argv) > 2 else []
    {"quantify": quantify, "isolate": isolate, "timeline": timeline}.get(
        step, lambda *_: sys.exit("usage: claims_diag.py quantify|isolate|timeline [minutes]"))(*arg)
CLAIMS_DIAG_EOF
python3 -m py_compile /tmp/claims_diag.py && echo "claims_diag.py ready"
```

## Step 1 - Quantify the failure

```bash
AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py quantify 30
```

Prints the request count per HTTP status for the last 30 minutes, the 4xx
share, and the 5xx count. The expected signature is a large 422 share with
**zero 5xx**: the service is up but rejecting input, which rules out an outage.

## Step 2 - Isolate the failing inputs

```bash
AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py isolate 30
```

Breaks the 422s down by `claim_type` and by `ref_len` for the version that is
producing them, and flags any value rejected 100% of the time. A value that
always fails while its neighbours pass (for example `ref_len=64` failing while
`63` passes, or one `claim_type` always failing) is the exact input the new
validation wrongly rejects.

## Step 3 - Find the version boundary

```bash
AWS_PROFILE=725425002230 python3 /tmp/claims_diag.py timeline 360
```

Shows every version seen in the last 6 hours, with request and rejection
counts and first/last-seen times, then names the last clean version and the
first failing one. If every row is failing, rerun with a larger window (for
example `timeline 1440`).

## Step 4 - Confirm the cause in the code

Using the GitHub integration, open the most recent merged change on `main` of
`chetansuri10101984-cloud/test2` that matches the failing version from Step 3.
Compare `app/validation.py` and `app/query.sql` against the previous version
and map each Step 2 fingerprint to a changed line:

1. **Reference length boundary.** `validate_reference` must accept lengths up
   to and including `MAX_REFERENCE_LEN` (64). A strict `<` where it should be
   `<=` rejects exactly-64-character references.
2. **claim_type allowlist.** `_query_allowlist()` in `app/validation.py` and
   the `ANY(ARRAY[...])` literal in `app/query.sql` must contain every value in
   `ALLOWED_CLAIM_TYPES` (and in the `claims.claim_type_allowlist` table). A
   dropped value is rejected 100% of the time.

## Step 5 - Root cause and fix

State the root cause as the specific commit and lines that introduced the
validation regression. Back it with the numbers from Steps 1 to 3: the 422
share, the 100%-rejected inputs, and the version boundary.

Recommend the minimal fix:
- Restore the reference-length comparison so lengths up to and including
  `MAX_REFERENCE_LEN` are accepted.
- Restore the dropped value(s) to the claim_type allowlist in both
  `app/validation.py` and `app/query.sql`.

Deliver the fix as a pull request against `chetansuri10101984-cloud/test2`
touching only those two files, so a human can review and merge it.
