# Rushbase in a Proxmox LXC container (bare metal mode)

Rushbase runs well in a Debian 12 LXC container using the bare-metal installer
(`--mode bare`): systemd, PostgreSQL and the server all live inside the container.
Docker mode inside LXC is possible but needs nesting and is not recommended.

## 1. Create the container

- Template: `debian-12-standard` (Ubuntu 22.04/24.04 also work).
- Resources: 2+ cores, 2 GiB RAM (4 GiB if the server also runs the web build), 16 GiB root disk
  (PostgreSQL, derived previews are stored on the share, not here).
- Network: a static address or DHCP reservation; the server binds to 127.0.0.1 and is reached
  through a reverse proxy or tunnel.
- Features: **`nesting=1`** is enough for systemd; add **`mount=cifs`** only if you mount the
  share inside the container (see below).

## 2. Privileged vs unprivileged

| | Unprivileged (default) | Privileged |
| --- | --- | --- |
| Security | recommended, root in the container is not root on the host | weaker isolation |
| `mount -t cifs` inside the container | **not possible** (the kernel refuses cifs mounts from a user namespace) | possible with the `mount=cifs` feature |
| Library access | bind-mount the share from the Proxmox host | mount inside the container or bind-mount from the host |

### Recommended: unprivileged container + host mount

1. Mount the SMB share on the Proxmox host (for example `/mnt/pve/rushbase`) with the
   options from ADR-0005 (`vers=3.1.1,cache=strict,actimeo=1,noperm,...`).
2. Map the container user. Rushbase runs as the system user `rushbase`; its UID inside the
   container is typically 100 (system range). In an unprivileged container this appears on the
   host as `100000 + UID`. Mount the share with `uid=<host uid>,gid=<host gid>` so the container
   user can write, for example `uid=100100,gid=100100` when the `rushbase` user has UID 100.
   Check with `id rushbase` inside the container after installing.
3. Add the bind mount to `/etc/pve/lxc/<id>.conf`:

   ```
   mp0: /mnt/pve/rushbase,mp=/mnt/rushbase
   ```

4. Install Rushbase with `--library /mnt/rushbase` (do **not** pass `--smb`).

### Alternative: privileged container with an in-container mount

Enable the `mount=cifs` feature (Options > Features) and run the installer with
`--smb //nas/share --library /mnt/rushbase`. The installer writes the fstab entry and a
`chmod 600` credentials file. Use this only on a trusted host; a privileged container shares
the host's root.

## 3. Install

```sh
apt-get update && apt-get install -y git curl
git clone https://github.com/Elias02345/RushBase.git /root/rushbase-installer
sudo bash /root/rushbase-installer/install.sh --mode bare --yes --library /mnt/rushbase
```

Check afterwards with `sudo bash /opt/rushbase/doctor.sh`.

## Caveats

- **Clock and sleep:** make sure the container has the correct time zone; the stability window of
  the input scanner compares mtimes.
- **Backups:** `/var/lib/rushbase/backups` lives on the container disk. Include it (and
  `/opt/rushbase/.env`) in your Proxmox backup job. Losing `.env` loses the encryption/signing
  secrets.
- **Snapshots:** a Proxmox snapshot before large updates is cheap insurance on top of the
  built-in database backup and automatic rollback.
- **inotify:** changes made by other SMB clients are never visible through inotify; Rushbase
  polls by design (ADR-0005), so nothing extra is needed.
