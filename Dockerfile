FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5555

# rsync and ssh let the live-page publisher push from inside the container
# (docs/sharing.md, "Publishing a live page").
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5555

# Serve with waitress instead of Flask's dev server. Host/port still come
# from the HOST/PORT env vars above so `docker run -e PORT=...` keeps working.
CMD ["sh", "-c", "waitress-serve --host=$HOST --port=$PORT app:app"]
