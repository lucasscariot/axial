# Container image for this axial project. `axial deploy` builds and runs it on Cloud Run
# behind Identity-Aware Proxy; `axial guide deploy` explains why the service must never be
# reachable without authentication.
#
#   docker build -t my-dashboards .
#
# AXIAL_REF is this project's one engine pin: the axial release (or commit) the image
# installs, with its locked dependencies. CI checks the project with this image, and
# `axial check` fails if a release pinned here falls outside `engine:` in axial.yaml.

FROM python:3.13-slim AS engine
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /usr/local/bin/uv
ARG AXIAL_REF=__AXIAL_REF__
ADD https://github.com/lucasscariot/axial/archive/${AXIAL_REF}.tar.gz /tmp/axial.tar.gz
ENV UV_PROJECT_ENVIRONMENT=/opt/axial UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
RUN mkdir /tmp/axial && tar -xzf /tmp/axial.tar.gz --strip-components=1 -C /tmp/axial \
    && cd /tmp/axial \
    && uv sync --frozen --no-dev --no-editable --extra duckdb --no-managed-python --python /usr/local/bin/python3 \
    && /opt/axial/bin/axial --version

FROM python:3.13-slim
COPY --from=engine /opt/axial /opt/axial
RUN useradd --uid 10001 --create-home axial
WORKDIR /app
# .dockerignore is an allowlist, so this copies axial.yaml, dashboards/ and queries/ only
COPY . /app
# history (storage.path) is written under .axial/; it lasts only as long as the container
RUN mkdir -p /app/.axial && chown axial:axial /app/.axial
ARG APP_REVISION=""
ENV PATH=/opt/axial/bin:$PATH PYTHONUNBUFFERED=1 PORT=8080 AXIAL_APP_REVISION=$APP_REVISION
USER axial
EXPOSE 8080
CMD ["axial", "serve"]
