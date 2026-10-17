#!/usr/bin/env bash
# Optional CIFS/SMB mount for the media library (ADR-0005). Sourced by install.sh after
# prereq.sh. Only used when the operator passes --smb; never configured by default.
# shellcheck disable=SC2034

RB_FSTAB="${RUSHBASE_FSTAB:-/etc/fstab}"
RB_SMB_CRED="${RUSHBASE_SMB_CRED:-/etc/rushbase/smb.credentials}"
RB_SMB_BEGIN="# >>> rushbase smb (managed by install.sh) >>>"
RB_SMB_END="# <<< rushbase smb <<<"

# smb_options UID GID [EXTRA]: the mount options recommended by ADR-0005 for the server (rw).
smb_options() {
  local uid="$1" gid="$2" extra="${3:-}" opts
  opts="credentials=${RB_SMB_CRED},vers=3.1.1,cache=strict,actimeo=1,uid=${uid},gid=${gid}"
  opts+=",file_mode=0660,dir_mode=0770,noperm,_netdev,nofail,x-systemd.requires=network-online.target"
  if [[ -n "${extra}" ]]; then
    opts+=",${extra}"
  fi
  printf '%s' "${opts}"
}

# smb_write_credentials USER PASSWORD [DOMAIN]
smb_write_credentials() {
  local user="$1" password="$2" domain="${3:-}"
  install -d -m 0755 "$(dirname "${RB_SMB_CRED}")"
  (
    umask 077
    {
      printf 'username=%s\n' "${user}"
      printf 'password=%s\n' "${password}"
      if [[ -n "${domain}" ]]; then
        printf 'domain=%s\n' "${domain}"
      fi
    } >"${RB_SMB_CRED}"
  )
  chown root:root "${RB_SMB_CRED}" 2>/dev/null || true
  chmod 600 "${RB_SMB_CRED}"
}

# smb_write_fstab SHARE MOUNTPOINT OPTIONS: replaces the managed block in fstab (idempotent).
smb_write_fstab() {
  local share="$1" mountpoint="$2" options="$3" tmp
  touch "${RB_FSTAB}"
  tmp="$(mktemp "${RB_FSTAB}.XXXXXX")"
  awk -v b="${RB_SMB_BEGIN}" -v e="${RB_SMB_END}" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "${RB_FSTAB}" >"${tmp}"
  {
    echo "${RB_SMB_BEGIN}"
    echo "${share// /\\040} ${mountpoint// /\\040} cifs ${options} 0 0"
    echo "${RB_SMB_END}"
  } >>"${tmp}"
  cat "${tmp}" >"${RB_FSTAB}"
  rm -f "${tmp}"
}

# smb_configure SHARE MOUNTPOINT USER PASSWORD [DOMAIN] [EXTRA_OPTIONS]
smb_configure() {
  local share="$1" mountpoint="$2" user="$3" password="$4" domain="${5:-}" extra="${6:-}"
  local uid gid
  if [[ ! "${share}" =~ ^//[^/]+/.+ ]]; then
    fail "Invalid SMB share '${share}' (expected //host/share)."
  fi
  uid="${RB_SMB_UID:-$(id -u "${RB_USER}")}"
  gid="${RB_SMB_GID:-$(id -g "${RB_USER}")}"
  smb_write_credentials "${user}" "${password}" "${domain}"
  mkdir -p "${mountpoint}"
  smb_write_fstab "${share}" "${mountpoint}" "$(smb_options "${uid}" "${gid}" "${extra}")"
  ok "SMB mount ${share} -> ${mountpoint} written to ${RB_FSTAB} (credentials: ${RB_SMB_CRED}, mode 600)"
  if [[ "${RB_SMB_SKIP_MOUNT:-0}" == "1" ]]; then
    return 0
  fi
  systemctl daemon-reload || true
  if mountpoint -q "${mountpoint}" || mount "${mountpoint}"; then
    ok "Mounted ${mountpoint}"
  else
    warn "Mounting ${mountpoint} failed. In an unprivileged LXC container cifs mounts are not possible; see deploy/lxc/README.md."
  fi
}
