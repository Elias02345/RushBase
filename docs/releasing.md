# Releasing

One command bumps the version everywhere, one tag push builds and publishes every installable.

## Cut a release

```sh
git switch main && git pull                      # releases come from main (use --branch for others)
scripts/release.sh 0.9.0                         # or 0.9.0-rc.1; add --dry-run to preview the diff
git push origin main
git push origin v0.9.0
```

`release.sh` requires a clean tree, bumps all manifests (root and package `pyproject.toml`,
`uv.lock`, every `package.json`, `tauri.conf.json`, `Cargo.toml` and the `Cargo.lock` entry), moves
the CHANGELOG `[Unreleased]` section to `[0.9.0] - <date>`, commits `chore(release): v0.9.0` and
creates an annotated tag (signed with `git tag -s` when `user.signingkey` is set, otherwise an
unsigned annotated tag and a warning). It never pushes. Write the CHANGELOG entries before releasing.

`scripts/check-version.sh v0.9.0` fails when any manifest differs from the tag (CI runs it first).
Pre-release versions are written PEP 440 style in Python files (`0.9.0rc1`).

## What the tag push does (`.github/workflows/desktop.yml`)

1. `verify`: version check, creates the draft release for the tag.
2. `build` (Linux, Windows, macOS Apple Silicon): installers into the draft, plus the headless worker
   `rushbase-<version>-<os>-<arch>[.exe]` (the PyInstaller binary, no GUI needed for
   `rushbase enroll` / `rushbase process`). `latest.json` for the Tauri updater is uploaded when the
   repository variable `RUSHBASE_UPDATER_PUBKEY` and the signing secrets exist.
3. `image`: pushes `ghcr.io/elias02345/rushbase-server:<version>` (+ `latest`) and
   `rushbase-worker:<version>-{cpu,cuda,vulkan,rocm}` (+ `latest-<variant>`); server and worker-cpu
   are amd64 + arm64. Moving tags are only set for versions without a `-` suffix.
4. `arch`: builds `rushbase-bin-<version>-1-x86_64.pkg.tar.zst` from the release `.deb` in an
   `archlinux` container (`makepkg` as a build user, `desktop-file-validate`, icon check) and
   attaches it with `PKGBUILD` and `.SRCINFO`.
5. `publish`: `scripts/ci/publish-release.sh` writes `SHA256SUMS`, builds the notes (CHANGELOG
   section plus install table), marks versions below 1.0.0 or with a suffix as **pre-release**
   (`latest` only for stable) and publishes the draft. It refuses when an expected asset is missing.

Verify after the run: the release page lists installers for each OS, the three CLI binaries, the
`.pkg.tar.zst`, `SHA256SUMS`; `docker pull ghcr.io/elias02345/rushbase-server:<version>` works;
Downloading the package and running `sudo pacman -U ./<file>` installs it and Rushbase appears in the KDE launcher. (`pacman -U <url>` fetches a `.sig` as well; until packages are GPG-signed, install from the downloaded file.)

A failed run leaves a draft release: fix the cause and re-run the workflow (assets are uploaded with
overwrite) or delete the draft; never move a published tag.

## One-time setup for the owner

- **GHCR visibility.** New packages are private. After the first release open
  *GitHub, profile, Packages*, pick `rushbase-server` and `rushbase-worker`, *Package settings,
  Change visibility, Public*. (Tokens cannot do this via API for user-owned packages.)
- **Updater key (optional).** `pnpm tauri signer generate`, store the private key and password as
  secrets `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, the public key as the
  repository variable `RUSHBASE_UPDATER_PUBKEY` (see `docs/companion.md`).
- **Signing (optional, removes the OS warnings).** macOS: `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`,
  `APPLE_TEAM_ID`. Windows: `WINDOWS_CERTIFICATE` (base64 PFX), `WINDOWS_CERTIFICATE_PASSWORD`.
  Without them the apps are unsigned; the release notes explain how to open them.
- **Signed tags.** Configure `user.signingkey` (and publish the key) so `release.sh` signs the tag.

## Optional: publish to the AUR

The release already carries an installable package. For `yay -S rushbase-bin` the owner needs an AUR
account with an SSH key:

```sh
git clone ssh://aur@aur.archlinux.org/rushbase-bin.git && cd rushbase-bin
# take PKGBUILD.rushbase-bin and SRCINFO.rushbase-bin from the release (sha256 already filled in)
cp ../PKGBUILD.rushbase-bin PKGBUILD && cp ../SRCINFO.rushbase-bin .SRCINFO
git add PKGBUILD .SRCINFO && git commit -m "rushbase-bin 0.9.0" && git push
```

## Server installs on a tag

`scripts/compose-up.sh` runs the published `rushbase-server:<version>` image when the checkout is
exactly on a `v*` tag (or `RUSHBASE_SERVER_IMAGE` is set in `.env`) and falls back to a local build
when the image cannot be pulled. Installs following `main` or `dev` keep building locally.
