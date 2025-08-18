FROM python:3.12.11-slim

# установить make
RUN apt-get update \
 && apt-get install -y --no-install-recommends make \
 && rm -rf /var/lib/apt/lists/*

# установить Poetry
RUN pip install poetry

WORKDIR /app
COPY pyproject.toml poetry.lock* ./

# установить все зависимости, включая alembic и faststream
RUN poetry install --no-root

# теперь копируем код и Makefile
COPY Makefile ./
COPY src/ ./src/
COPY migrations/ ./migrations/

# точка входа — запустить make start
CMD ["make", "start-prod"]