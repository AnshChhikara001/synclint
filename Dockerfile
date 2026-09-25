# Two stages so that uv, which installs from the lockfile, stays out of the
# image the Action runs. Both on bookworm, so the virtual environment's
# interpreter path means the same thing in the second stage as in the first.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /synclint
# No bytecode compiled ahead: the image is built on every run, so compiling
# every module here costs more than compiling the ones imported, at import.
ENV UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
# git reads both revisions of the change. The workspace GitHub mounts is owned
# by the runner's user, not this container's root, and git refuses a repository
# owned by someone else unless told the directory is safe.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
COPY --from=build /synclint/.venv /synclint/.venv
ENV PATH=/synclint/.venv/bin:$PATH
ENTRYPOINT ["python", "-m", "synclint.action"]
