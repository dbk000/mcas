FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git tmux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        numpy \
        pandas \
        pyarrow \
        psycopg[binary] \
        openpyxl

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL=postgresql://trading:trading@postgres:5432/trading

# Sandbox mounted at /app/GX_Research at runtime; see run instructions in README.
CMD ["bash"]
