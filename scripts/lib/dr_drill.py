"""API and database helper for ``scripts/dr-drill.sh`` (disaster-recovery drill).

Standard library plus psycopg and SQLAlchemy (both server dependencies). Every command reads its
target from the environment: ``DR_BASE_URL``, ``DR_USER``, ``DR_PASS``, ``DR_SETUP_TOKEN`` and,
for the ``db`` command, ``RUSHBASE_DATABASE_URL`` (any database on the server; only databases
named ``rushbase_dr_*`` are ever created or dropped).
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = os.environ.get("DR_BASE_URL", "http://127.0.0.1:18090")
USER = os.environ.get("DR_USER", "drill-admin")
PASSWORD = os.environ.get("DR_PASS", "")
SETUP_TOKEN = os.environ.get("DR_SETUP_TOKEN", "")
SEARCH_QUERY = "uhd"


class Api:
    """Tiny cookie and CSRF aware JSON client."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method: str, path: str, body: Any = None) -> Any:
        """Send one request; exits with the server's answer on an HTTP error."""
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE + path, data=data, method=method)  # noqa: S310
        request.add_header("Content-Type", "application/json")
        if self.csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"[FATAL] {method} {path} -> {exc.code}: {exc.read()[:300]!r}"
            ) from exc
        return json.loads(raw) if raw else None

    def login(self) -> None:
        """Sign in as the drill admin."""
        out = self.call("POST", "/api/v1/auth/login", {"username": USER, "password": PASSWORD})
        self.csrf = out["csrf_token"]


def logged_in() -> Api:
    """A client that is signed in."""
    api = Api()
    api.login()
    return api


def cmd_wait_health(timeout: str = "90") -> None:
    """Wait until ``/health`` answers."""
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/health", timeout=3).read()  # noqa: S310
            return
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise SystemExit("[FATAL] server did not become healthy")


def cmd_setup_status() -> None:
    """Print ``GET /api/v1/setup``."""
    print(json.dumps(Api().call("GET", "/api/v1/setup")))


def cmd_setup() -> None:
    """Run the first-run setup (admin + library)."""
    Api().call(
        "POST",
        "/api/v1/setup",
        {
            "username": USER,
            "password": PASSWORD,
            "library_name": "DR drill",
            "setup_token": SETUP_TOKEN,
        },
    )


def cmd_restore(name: str) -> None:
    """Restore the named backup through the setup endpoint."""
    Api().call("POST", "/api/v1/setup/restore", {"backup_name": name, "setup_token": SETUP_TOKEN})


def cmd_enroll_token() -> None:
    """Print a fresh worker enrollment token."""
    out = logged_in().call("POST", "/api/v1/admin/enrollment-tokens", {"note": "dr-drill"})
    print(out["token"])


def cmd_backup_names() -> None:
    """Print the backup names offered by the setup status, newest first."""
    for item in Api().call("GET", "/api/v1/setup")["backups"]:
        print(item["name"])


def all_assets(api: Api) -> list[dict[str, Any]]:
    """Every asset summary (keyset pagination)."""
    items: list[dict[str, Any]] = []
    cursor = ""
    while True:
        query = "limit=100" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        page = api.call("GET", f"/api/v1/assets?{query}")
        items.extend(page["items"])
        cursor = page.get("next_cursor") or ""
        if not cursor:
            return items


def cmd_wait_filed(minimum: str, timeout: str = "180") -> None:
    """Wait until at least ``minimum`` assets exist and the count stops changing."""
    api = logged_in()
    deadline = time.time() + float(timeout)
    previous = -1
    while time.time() < deadline:
        total = len(all_assets(api))
        if total >= int(minimum) and total == previous:
            return
        previous = total
        time.sleep(4)
    raise SystemExit("[FATAL] assets did not appear in time")


def cmd_edit(state_file: str) -> None:
    """Human edits: tag, rating, lock, title, plus a needs list with a confirmed pick."""
    api = logged_in()
    assets = sorted((a for a in all_assets(api) if a["status"] == "filed"), key=lambda a: a["id"])
    if len(assets) < 2:
        raise SystemExit("[FATAL] need at least two assets for the edits")
    first, second = assets[0], assets[1]
    field = api.call(
        "POST",
        "/api/v1/taxonomy/fields",
        {"key": "dr_drill", "label_de": "DR-Test", "label_en": "DR drill"},
    )
    api.call("POST", f"/api/v1/taxonomy/fields/{field['id']}/values", {"value": "recovery"})
    api.call(
        "PATCH",
        f"/api/v1/assets/{first['id']}",
        {"rating": 4, "locked": True, "add_tags": [{"field": "dr_drill", "value": "recovery"}]},
    )
    api.call("PATCH", f"/api/v1/assets/{second['id']}", {"rating": 2, "title": "DR drill title"})
    need = api.call(
        "POST",
        "/api/v1/needs",
        {"title": "DR drill list", "purpose": "recovery", "items": [{"description": "a clip"}]},
    )
    item = need["items"][0]["id"]
    api.call("PUT", f"/api/v1/needs/items/{item}/candidates/{first['id']}", {"status": "confirmed"})
    json_dump({"tagged": first["id"], "rated": second["id"], "need": need["id"]}, state_file)


def json_dump(data: Any, path: str) -> None:
    """Write sorted, indented JSON."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def json_load(path: str) -> Any:
    """Read JSON."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def cmd_snapshot(out: str) -> None:
    """Counts and fingerprints of everything a recovery must preserve."""
    api = logged_in()
    assets = []
    for summary in sorted(all_assets(api), key=lambda a: a["id"]):
        detail = api.call("GET", f"/api/v1/assets/{summary['id']}")
        ai = detail["ai"]
        assets.append(
            {
                "id": detail["id"],
                "filename": detail["filename"],
                "status": detail["status"],
                "content_rel_dir": detail["content_rel_dir"],
                "files": sorted(
                    f"{f['role']}:{f['rel_path']}:{f['blake3']}" for f in detail["files"]
                ),
                "shots": len(detail["shots"]),
                "title": detail["title"],
                "rating": ai["rating"],
                "locked": ai["locked"],
                "tags": sorted(
                    f"{t['field_key']}={t['value']}@{t['source']}#{t['shot_idx']}"
                    for t in ai["tags"]
                ),
                "transcript_segments": ai["transcript_segments"],
            }
        )
    needs = []
    for row in api.call("GET", "/api/v1/needs?limit=100")["items"]:
        need = api.call("GET", f"/api/v1/needs/{row['id']}")
        needs.append(
            {
                "id": need["id"],
                "title": need["title"],
                "items": [
                    {
                        "description": i["description"],
                        "status": i["status"],
                        "candidates": sorted(
                            f"{c['asset_id']}:{c['status']}:{c['source']}" for c in i["candidates"]
                        ),
                    }
                    for i in need["items"]
                ],
            }
        )
    search = api.call("GET", f"/api/v1/search?q={SEARCH_QUERY}&limit=50")
    snap = {
        "asset_count": len(assets),
        "assets": assets,
        "needs": needs,
        "search_assets": sorted(hit["asset"]["id"] for hit in search["items"]),
        "users": sorted(u["username"] for u in api.call("GET", "/api/v1/admin/users")),
    }
    json_dump(snap, out)
    print(
        f"{snap['asset_count']} assets, {sum(a['shots'] for a in assets)} shots, "
        f"{len(needs)} needs lists, {len(snap['search_assets'])} search hits"
    )


def cmd_compare(before: str, after: str, mode: str) -> None:
    """``backup``: identical snapshots. ``sidecars``: identical except database-only data."""
    a, b = json_load(before), json_load(after)
    if mode == "sidecars":
        for snap in (a, b):
            snap.pop("needs", None)
            snap.pop("users", None)
            # Only filed assets have sidecars; duplicates and unfiled input are re-scanned.
            snap["assets"] = [x for x in snap["assets"] if x["status"] == "filed"]
            snap["asset_count"] = len(snap["assets"])
    problems: list[str] = []
    for key in sorted(a):
        if a[key] == b.get(key):
            continue
        if key != "assets":
            problems.append(f"{key}: {a[key]!r} != {b.get(key)!r}")
            continue
        after_by_id = {x["id"]: x for x in b.get(key, [])}
        for item in a[key]:
            other = after_by_id.get(item["id"]) or {}
            diff = {k: (v, other.get(k)) for k, v in item.items() if v != other.get(k)}
            if diff:
                problems.append(f"asset {item['id']}: {diff}")
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)
    print("identical")


def cmd_db(action: str, name: str) -> None:
    """Create (fresh) or drop one ``rushbase_dr_*`` database on the dev server."""
    import psycopg
    from sqlalchemy.engine import make_url

    if not name.startswith("rushbase_dr_"):
        raise SystemExit("[FATAL] refusing to touch a database outside rushbase_dr_*")
    url = make_url(os.environ["RUSHBASE_DATABASE_URL"]).set(
        drivername="postgresql", database="postgres"
    )
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as conn:
        if action in {"drop", "create"}:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if action == "create":
            conn.execute(f'CREATE DATABASE "{name}"')
        elif action != "drop":
            raise SystemExit(f"[FATAL] unknown db action {action}")


def main() -> None:
    """Dispatch ``dr_drill.py <command> [args]``."""
    func = globals().get(f"cmd_{sys.argv[1].replace('-', '_')}")
    if func is None:
        raise SystemExit(f"[FATAL] unknown command {sys.argv[1]}")
    func(*sys.argv[2:])


if __name__ == "__main__":
    main()
