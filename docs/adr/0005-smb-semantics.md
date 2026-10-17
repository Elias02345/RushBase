# ADR-0005: Working with SMB shares

- Status: Accepted
- Date: 2026-10-13

## Context
Server, workers and companion apps access the same SMB share through their own mounts. Findings:

- Rename inside one share is a single server-side operation (effectively atomic for clients). If the
  target exists and replace is off → `STATUS_OBJECT_NAME_COLLISION`; renaming a directory that contains
  files open by another client fails with access denied. Not atomic across shares/mounts.
- inotify does not see changes made by other SMB clients; mtime may lag (`actimeo`, default 1 s).
  Explorer/Finder write directly under the final name, so partial files are visible.
- Case: TrueNAS SMB datasets default to case-insensitive (immutable after creation); Samba
  `case sensitive = auto`; the Linux cifs client is case-sensitive unless `nocase`.
- Windows: reserved characters `< > : " / \ | ? *` and 0–31, reserved names (CON, PRN, AUX, NUL,
  COM1–9, LPT1–9, also with extension), no trailing dot/space, MAX_PATH 260 unless long paths enabled.
- cifs options: `vers=3.1.1`, `cache=strict` (default, required for multiple clients; never `loose`),
  `actimeo`, `uid/gid/file_mode/dir_mode`, `noperm`, `mapposix`, `noserverino`.

## Decision
1. Originals are never modified. The only file operation on originals is the server's move from
   `Input/` to `Content/` **by rename on the same share** (spec §2.5). Rename never overwrites: on
   collision a `_2`, `_3` … suffix is chosen; `EACCES/EBUSY/EPERM` are treated as "in use" and retried
   with backoff, then reported. Card structures move as one directory rename.
2. Files Rushbase writes (sidecars, XMP, journal, library.json, derived) use write-to-temp in the same
   directory (`.<name>.rbtmp`) → fsync → rename.
3. Stability: a file is ready when size and mtime are unchanged across the configured window (default
   2 scans / 120 s, spec §4), it opens for reading, its name is not ignored, and for multi-file units
   (card structures, sequences) the whole unit is quiet. The probe stage additionally validates the
   container (ffprobe reads it).
4. Polling only (default 60 s, ≥ 2× `actimeo`); filesystem events are an optional fast path, never relied upon.
5. Identity comparisons use NFC + casefold keys; collisions under that key are flagged, never overwritten.
6. Generated names pass a sanitiser (reserved characters/names, trailing dot/space, component ≤ 255,
   full relative path budget so that typical roots stay < 240 chars on Windows).
7. Recommended mount (server, rw):
   `vers=3.1.1,cache=strict,actimeo=1,uid=<svc>,gid=<svc>,file_mode=0660,dir_mode=0770,noperm`.
   Workers may mount read-only except `.rushbase/derived/` where the NAS permits. `doctor.sh` warns on
   `cache=loose`, `vers<3`, `actimeo>5`.

## Consequences
New files appear after the stability window; large polling trees cost metadata round trips (scanner
only walks `Input/`). Server-specific oplock/share-mode behaviour is UNVERIFIED and not relied upon.

## Sources
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/1d2673a8-8fb9-4868-920a-775ccaa30cf8
- https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
- https://man7.org/linux/man-pages/man8/mount.cifs.8.html
- https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html
- https://www.truenas.com/docs/scale/25.10/scaletutorials/shares/smb/
