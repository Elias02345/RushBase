#!/usr/bin/env bash
# Regenerate the SigLIP 2 golden fixture in a throw-away container (host stays clean).
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="rushbase-m4-ref"

docker rm -f "${NAME}" >/dev/null 2>&1 || true
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT
docker run --name "${NAME}" -e HF_HOME=/tmp/hf -v "${HERE}:/fixtures" python:3.12.14-slim-bookworm bash -c "
  set -Eeuo pipefail
  pip install -q --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
  pip install -q --no-cache-dir 'transformers>=4.50' pillow numpy sentencepiece protobuf
  python /fixtures/make_siglip_fixture.py /fixtures
  chown $(id -u):$(id -g) /fixtures/siglip2_golden.json /fixtures/siglip_test.png"
