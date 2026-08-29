FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Portfolio/audit state -- see docs/decisions.md for the note on swapping
# this for Table Storage so it survives container restarts/scale-out.
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
