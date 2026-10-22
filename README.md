# claims-api

Insurance claims intake microservice. Accepts and stores claims submitted by
upstream channels, backed by PostgreSQL. Runs on Amazon EKS.

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/claims` | File a claim. Body: `{ "claim_type": "health\|property\|auto", "reference": "<string>" }`. Returns `201` on success, `422` on validation failure. |
| `GET`  | `/claims/{id}` | Fetch a claim. `200` / `404`. |
| `GET`  | `/healthz` | Liveness/readiness probe (process + DB connectivity). |

## Validation rules

- `reference`: non-empty string, length up to and including `MAX_REFERENCE_LEN` (64).
- `claim_type`: one of `health`, `property`, `auto`.

Validation failures return **HTTP 422 Unprocessable Entity**.

## Layout

```
app/
  main.py         Flask entrypoint + routes, structured JSON access logging
  handler.py      request handlers (file/fetch claim)
  validation.py   request validation rules
  db.py           PostgreSQL data layer (psycopg)
  query.sql       claim_type allowlist query
deploy/
  schema.sql      claims schema (claims table + claim_type_allowlist seed)
  k8s/            Kubernetes manifests (namespace, Deployment, Service)
Dockerfile        arm64 (Graviton) image
requirements.txt  Flask, gunicorn, psycopg
```

## Build & deploy

```bash
# build + push (arm64, for t4g/Graviton nodes)
docker build --platform linux/arm64 -t <ecr>/claims-api:<tag> .
docker push <ecr>/claims-api:<tag>

# deploy
kubectl apply -f deploy/k8s/
```

The container reads its database connection from the `claims-db` Kubernetes
secret. Each request is logged as one JSON line to stdout and shipped to
CloudWatch Logs.

## Release history

- `v1.4.2` — current production release.
