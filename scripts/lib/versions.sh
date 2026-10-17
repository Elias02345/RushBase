#!/usr/bin/env bash
# Shared helpers for scripts/release.sh and scripts/check-version.sh: the list of files that carry
# the product version and how to read them. Source this file; it has no side effects.
# shellcheck disable=SC2034  # the arrays are consumed by the scripts that source this file

# Every file carrying the version (relative to the repository root).
VERSION_PYPROJECTS=(pyproject.toml core/pyproject.toml server/pyproject.toml worker/pyproject.toml)
VERSION_PACKAGE_JSONS=(package.json web/package.json desktop/package.json
  packages/ui/package.json packages/api-client/package.json)
VERSION_TAURI_CONF=desktop/src-tauri/tauri.conf.json
VERSION_CARGO_TOML=desktop/src-tauri/Cargo.toml
VERSION_CARGO_LOCK=desktop/src-tauri/Cargo.lock
VERSION_UV_LOCK=uv.lock
VERSION_UV_PACKAGES=(rushbase rushbase-core rushbase-server rushbase-worker)

# is_semver <version>: X.Y.Z or X.Y.Z-rc.N (a pre-release suffix is dot-separated identifiers).
is_semver() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$ ]]
}

# pep440 <semver>: 0.9.0-rc.1 -> 0.9.0rc1 (the form uv writes into pyproject and uv.lock).
pep440() {
  local v="$1" core pre num
  core="${v%%-*}"
  if [[ "${v}" != *-* ]]; then
    printf '%s' "${core}"
    return
  fi
  pre="${v#*-}"
  num="${pre//[!0-9]/}"
  case "${pre}" in
    rc*) printf '%src%s' "${core}" "${num:-0}" ;;
    alpha*|a.*) printf '%sa%s' "${core}" "${num:-0}" ;;
    beta*|b.*) printf '%sb%s' "${core}" "${num:-0}" ;;
    *) printf '%s.dev%s' "${core}" "${num:-0}" ;;
  esac
}

# first_toml_version <file>: the first `version = "..."` line.
first_toml_version() {
  sed -n 's/^version = "\(.*\)"$/\1/p' "$1" | head -n1
}

# json_version <file>: the first two-space-indented `"version": "..."` entry.
json_version() {
  sed -n 's/^  "version": "\([^"]*\)".*$/\1/p' "$1" | head -n1
}

# lock_version <file> <package>: version of a package in Cargo.lock / uv.lock.
lock_version() {
  awk -v pkg="$2" '
    $0 == "name = \"" pkg "\"" { found = 1; next }
    found && /^version = / { gsub(/^version = "|"$/, ""); print; exit }
  ' "$1"
}

# print_versions: "<where> <version> <kind>" for every carrier; kind is semver or pep440.
print_versions() {
  local f p
  for f in "${VERSION_PYPROJECTS[@]}"; do
    printf '%s %s pep440\n' "${f}" "$(first_toml_version "${f}")"
  done
  for f in "${VERSION_PACKAGE_JSONS[@]}" "${VERSION_TAURI_CONF}"; do
    printf '%s %s semver\n' "${f}" "$(json_version "${f}")"
  done
  printf '%s %s semver\n' "${VERSION_CARGO_TOML}" "$(first_toml_version "${VERSION_CARGO_TOML}")"
  printf '%s[rushbase-desktop] %s semver\n' "${VERSION_CARGO_LOCK}" \
    "$(lock_version "${VERSION_CARGO_LOCK}" rushbase-desktop)"
  for p in "${VERSION_UV_PACKAGES[@]}"; do
    printf '%s[%s] %s pep440\n' "${VERSION_UV_LOCK}" "${p}" "$(lock_version "${VERSION_UV_LOCK}" "${p}")"
  done
}
