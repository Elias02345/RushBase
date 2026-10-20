# syntax=docker/dockerfile:1
# Headless Rushbase worker (`rushbase process`) for a processing PC or a render node.
#
#   docker build -f deploy/docker/worker.Dockerfile -t rushbase-worker:cpu .
#   docker build -f deploy/docker/worker.Dockerfile --build-arg VARIANT=cuda -t rushbase-worker:cuda .
#   docker build -f deploy/docker/worker.Dockerfile --build-arg VARIANT=vulkan -t rushbase-worker:vulkan .
#   docker build -f deploy/docker/worker.Dockerfile --build-arg VARIANT=rocm -t rushbase-worker:rocm .
#
# VARIANT only chooses the base image and the userspace GPU libraries. The pinned llama.cpp and
# whisper.cpp builds (worker/binaries.lock.json) and the models are downloaded with SHA-256
# verification by `rushbase models pull` into the data volume, matching the detected backend, so the
# image stays small and a lock bump needs no rebuild. GPU access comes from the container runtime:
#   cuda:   docker run --gpus all ...
#   vulkan: docker run --device /dev/dri ...
#   rocm:   docker run --device /dev/kfd --device /dev/dri --group-add video ...
#
# Usage (library and data are volumes, nothing is baked in):
#   docker run --rm -v /mnt/rushbase:/library -v rushbase-worker:/data \
#     -e RUSHBASE_SERVER_URL=http://server:8080 rushbase-worker:cpu enroll --server ... --token ... --name pc
#   docker run --rm ... rushbase-worker:cpu models pull
#   docker run -d --restart unless-stopped ... rushbase-worker:cpu process
ARG VARIANT=cpu
ARG CPU_BASE=python:3.12-slim-bookworm
ARG CUDA_BASE=nvidia/cuda:12.8.1-runtime-ubuntu24.04
ARG VULKAN_BASE=ubuntu:24.04
ARG ROCM_BASE=ubuntu:24.04

FROM python:3.15-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed
COPY pyproject.toml uv.lock .python-version ./
COPY core ./core
COPY server ./server
COPY worker ./worker
RUN uv sync --frozen --no-dev --package rushbase-worker

FROM ${CPU_BASE} AS base-cpu
ENV EXTRA_PACKAGES=""

FROM ${CUDA_BASE} AS base-cuda
ENV EXTRA_PACKAGES=""

FROM ${VULKAN_BASE} AS base-vulkan
ENV EXTRA_PACKAGES="libvulkan1 mesa-vulkan-drivers"

FROM ${ROCM_BASE} AS base-rocm
ENV EXTRA_PACKAGES="libvulkan1 mesa-vulkan-drivers"

FROM base-${VARIANT} AS runtime
ARG VARIANT
ENV DEBIAN_FRONTEND=noninteractive
# ffmpeg and ExifTool's perl are runtime tools of the technical stages; libgomp/libglib are needed
# by the bundled inference binaries and OpenCV.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg perl libgomp1 libglib2.0-0 libgl1 ${EXTRA_PACKAGES} \
 && rm -rf /var/lib/apt/lists/*
# The managed Python from the build stage (the non-slim bases do not ship Python 3.12).
COPY --from=build /opt/python /opt/python
COPY --from=build /app /app
RUN useradd --system --uid 10001 --create-home rushbase \
 && mkdir -p /data /library \
 && chown rushbase /data
ENV PATH="/app/.venv/bin:$PATH" \
    RUSHBASE_DATA_DIR=/data \
    RUSHBASE_LIBRARY_ROOT=/library \
    RUSHBASE_VARIANT=${VARIANT}
USER rushbase
VOLUME ["/data"]
# Healthy when the CLI starts and the hardware probe runs; real health is the server's worker list.
HEALTHCHECK --interval=60s --timeout=20s --retries=3 CMD ["rushbase", "version"]
ENTRYPOINT ["rushbase"]
CMD ["process"]
