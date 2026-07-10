FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5555

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5555

# Serve with waitress instead of Flask's dev server. Host/port still come
# from the HOST/PORT env vars above so `docker run -e PORT=...` keeps working.
CMD ["sh", "-c", "waitress-serve --host=$HOST --port=$PORT app:app"]
