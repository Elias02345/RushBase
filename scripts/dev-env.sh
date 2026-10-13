#!/usr/bin/env bash
# Source this file to use the project-local, isolated toolchains:
#   source scripts/dev-env.sh
# Nothing outside the repository (or the main checkout's .toolchain) is touched.

_rb_script="${BASH_SOURCE[0]:-$0}"
RUSHBASE_ROOT="$(cd "$(dirname "${_rb_script}")/.." && pwd)"
export RUSHBASE_ROOT

# Toolchain location: explicit override > nearest ".toolchain" in this directory or any
# parent (so linked worktrees under the main checkout reuse its toolchain) > ./.toolchain.
if [[ -z "${TOOLCHAIN_DIR:-}" ]]; then
  TOOLCHAIN_DIR="${RUSHBASE_ROOT}/.toolchain"
  _rb_dir="${RUSHBASE_ROOT}"
  while [[ "${_rb_dir}" != "/" ]]; do
    if [[ -d "${_rb_dir}/.toolchain" ]]; then
      TOOLCHAIN_DIR="${_rb_dir}/.toolchain"
      break
    fi
    _rb_dir="$(dirname "${_rb_dir}")"
  done
fi
export TOOLCHAIN_DIR

export UV_PYTHON_INSTALL_DIR="${TOOLCHAIN_DIR}/python"
export UV_CACHE_DIR="${TOOLCHAIN_DIR}/uv-cache"
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_BIN_DIR="${TOOLCHAIN_DIR}/bin"
export UV_TOOL_DIR="${TOOLCHAIN_DIR}/uv-tools"
export UV_TOOL_BIN_DIR="${TOOLCHAIN_DIR}/bin"
export RUSTUP_HOME="${TOOLCHAIN_DIR}/rustup"
export CARGO_HOME="${TOOLCHAIN_DIR}/cargo"
export COREPACK_HOME="${TOOLCHAIN_DIR}/corepack"
export npm_config_cache="${TOOLCHAIN_DIR}/npm-cache"
export PNPM_HOME="${TOOLCHAIN_DIR}/pnpm"
export PATH="${TOOLCHAIN_DIR}/bin:${PNPM_HOME}:${CARGO_HOME}/bin:${PATH}"
