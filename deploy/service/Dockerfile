FROM ubuntu:24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY deploy/service/requirements.txt /build/requirements.txt
COPY deploy/service/freeze-requirements.txt /build/freeze-requirements.txt
# Keep the CPU wheel on its own index; other dependencies resolve from PyPI.
RUN pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.14.0+cpu \
    && pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends binutils libpython3.12 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r freeze-requirements.txt
COPY setup.py pyproject.toml README.md LICENSE.md MANIFEST.in ./
COPY src/gaia ./src/gaia
COPY hub/agents/chat/python ./hub/agents/chat/python
COPY hub/agents/gaia/python ./hub/agents/gaia/python
RUN pip install --no-deps --no-build-isolation . ./hub/agents/chat/python ./hub/agents/gaia/python \
    && pip check \
    && python -c 'from gaia_agent.agent import GaiaAgentConfig'
RUN python -c 'from pathlib import Path; from gaia.llm.lemonade_embedded import EmbeddedLemonade; EmbeddedLemonade(home=Path("/opt/gaia-embedded")).install()'
RUN python hub/agents/gaia/python/packaging/freeze.py
COPY deploy/service/frozen_tools_smoke.py /build/deploy/service/frozen_tools_smoke.py
RUN python -c 'import runpy; from pathlib import Path; f=runpy.run_path("hub/agents/gaia/python/packaging/freeze.py"); build=f["build"]; build.__globals__.update(ENTRY=Path("/build/deploy/service/frozen_tools_smoke.py"), NAME="gaia-tools-smoke"); build(clean=False)'

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 ca-certificates curl libgomp1 libcurl4t64 libssl3t64 libatomic1 libdrm-amdgpu1 git tini procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 gaia \
    && useradd --uid 10001 --gid 10001 --home-dir /data gaia \
    && mkdir -p /data /workspace /run/gaia \
    && chown gaia:gaia /data /workspace /run/gaia \
    && chmod 700 /run/gaia
COPY --from=build /build/hub/agents/gaia/python/packaging/dist/gaia-agent /opt/gaia-agent
COPY --from=build /opt/gaia-embedded /opt/gaia-embedded
RUN set -eu; for binary in /opt/gaia-embedded/lemonade/dist/*/lemond; do \
    ldd "$binary" > /tmp/linkage; cat /tmp/linkage; \
    if grep -q 'not found' /tmp/linkage; then exit 1; fi; done; rm /tmp/linkage
ENV PATH="/opt/gaia-agent:$PATH" \
    GAIA_PYTHON_EXECUTABLE=/usr/bin/python3 \
    HOME=/data \
    XDG_RUNTIME_DIR=/run/gaia \
    GAIA_HOME=/data/.gaia \
    GAIA_SERVICE_WORKSPACE=/workspace \
    GAIA_SERVICE_LEMONADE_BUNDLE=/opt/gaia-embedded \
    PYTHONUNBUFFERED=1 \
    PORT=8080
USER gaia
WORKDIR /workspace
RUN --network=none --mount=type=bind,from=build,source=/build/hub/agents/gaia/python/packaging/dist/gaia-tools-smoke,target=/tmp/gaia-tools-smoke \
    /tmp/gaia-tools-smoke/gaia-tools-smoke
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s CMD curl --fail --silent --show-error --max-time 10 -H "Host: ${GAIA_SERVICE_ALLOWED_HOSTS%%,*}" "http://127.0.0.1:${PORT}/health"
ENTRYPOINT ["/usr/bin/tini", "--", "gaia-agent", "--service"]
