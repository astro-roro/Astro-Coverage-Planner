FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5555

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a normal user rather than root. A container that is only publishing
# a port does not need uid 0, and a bug that lets someone write a file should
# not be writing it as root into a mounted volume.
RUN useradd --system --create-home --uid 10001 acp \
    && mkdir -p /app/data \
    && chown -R acp:acp /app/data
USER acp

EXPOSE 5555

# Serve with waitress instead of Flask's dev server. Host/port still come
# from the HOST/PORT env vars above so `docker run -e PORT=...` keeps working.
CMD ["sh", "-c", "waitress-serve --host=$HOST --port=$PORT app:app"]
