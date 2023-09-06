# syntax = docker/dockerfile:1.3

### Base image
FROM python:3.11-slim-bookworm AS base

ENV APP_HOME /app
ENV VIRTUAL_ENV /venv
ENV PYTHONPATH $APP_HOME
ENV POETRY_HOME /poetry
ENV PATH $VIRTUAL_ENV/bin:$POETRY_HOME/bin:$PATH
ENV PIP_NO_CACHE_DIR 1

### Build python dependencies
FROM base AS builder
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR $APP_HOME
COPY requirements.txt ./

# hadolint ignore=DL3013
RUN --mount=type=cache,target=/root/.cache \
    python -m venv /venv && \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# Production image
FROM base as runtime
ENV PATH /venv/bin:$PATH

COPY --from=builder /venv /venv

WORKDIR $APP_HOME
COPY . ./

CMD [ "python", "main.py" ]
