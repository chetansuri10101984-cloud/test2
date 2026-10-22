# claims-api container image. Built for arm64 (the cluster runs t4g/Graviton
# nodes).
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# System deps for psycopg binary are bundled in the wheel, so no apt needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8080

# Unbuffered stdout so the app's structured JSON access logs flush immediately
# (so fluent-bit -> CloudWatch sees them in real time).
ENV PYTHONUNBUFFERED=1

# gunicorn with a few workers so the service handles concurrent load during
# the incident-generation phase. The app emits its own structured JSON access
# log per request (see main.py _log()), so gunicorn's built-in access log is
# turned OFF entirely — leaving it on with an empty format emits blank lines.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", \
     "--access-logfile", "/dev/null", "app.main:app"]
