"""Server-rendered consent and error pages of the authorization endpoint (de/en)."""

from html import escape

STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "title": "Zugriff erlauben",
        "wants": "möchte auf deine Rushbase-Bibliothek zugreifen.",
        "redirect": "Weiterleitung nach",
        "scopes": "Angefragte Berechtigungen",
        "signed_in": "Angemeldet als",
        "username": "Benutzername",
        "password": "Passwort",
        "allow": "Erlauben",
        "deny": "Ablehnen",
        "login_hint": "Melde dich mit deinem Rushbase-Konto an, um den Zugriff zu erlauben.",
        "bad_login": "Anmeldung fehlgeschlagen.",
        "too_many": "Zu viele Versuche. Bitte später erneut versuchen.",
        "forbidden": "Nur Editoren und Administratoren dürfen Zugriff erlauben.",
        "error_title": "Anfrage nicht möglich",
        "unavailable": "MCP ist nicht eingerichtet: RUSHBASE_PUBLIC_URL fehlt.",
        "note": "Du kannst den Zugriff jederzeit unter Administration > MCP / Claude widerrufen.",
    },
    "en": {
        "title": "Allow access",
        "wants": "wants to access your Rushbase library.",
        "redirect": "Redirects to",
        "scopes": "Requested permissions",
        "signed_in": "Signed in as",
        "username": "Username",
        "password": "Password",
        "allow": "Allow",
        "deny": "Deny",
        "login_hint": "Sign in with your Rushbase account to allow access.",
        "bad_login": "Sign-in failed.",
        "too_many": "Too many attempts. Please try again later.",
        "forbidden": "Only editors and administrators can grant access.",
        "error_title": "Request not possible",
        "unavailable": "MCP is not set up: RUSHBASE_PUBLIC_URL is missing.",
        "note": "You can revoke access at any time under Administration > MCP / Claude.",
    },
}

SCOPE_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "read": "Bibliothek lesen und durchsuchen",
        "annotate": "Tags, Bewertungen und Notizen setzen",
        "taxonomy": "Vokabular vorschlagen und pflegen",
        "rules": "Ablageregeln vorschlagen und aktivieren",
        "needs": "Bedarfslisten verwalten",
        "processing": "Analyse erneut anstoßen",
        "offline_access": "Angemeldet bleiben (Refresh-Token)",
    },
    "en": {
        "read": "Read and search the library",
        "annotate": "Set tags, ratings and notes",
        "taxonomy": "Propose and maintain the vocabulary",
        "rules": "Propose and activate filing rules",
        "needs": "Manage needs lists",
        "processing": "Queue analysis again",
        "offline_access": "Stay signed in (refresh token)",
    },
}

_CSS = """
:root{color-scheme:light dark;--bg:#f6f6f4;--fg:#1c1c1a;--card:#fff;--muted:#6b6b66;--line:#dcdcd6;
--accent:#1f5eff;--accent-fg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecece8;--card:#1d1d1b;--muted:#9a9a93;
--line:#34342f;--accent:#6e95ff;--accent-fg:#0b0b0a}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;
display:grid;min-height:100vh;place-items:center;padding:16px;box-sizing:border-box}
main{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;
max-width:440px;width:100%}
h1{font-size:1.25rem;margin:0 0 8px}p{margin:8px 0}.muted{color:var(--muted);font-size:.9rem}
ul{padding-left:20px;margin:8px 0}label{display:block;margin-top:12px;font-size:.9rem}
input{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--line);
border-radius:8px;background:transparent;color:inherit;font:inherit}
.row{display:flex;gap:8px;margin-top:20px}button{flex:1;padding:10px;border-radius:8px;
border:1px solid var(--line);background:transparent;color:inherit;font:inherit;cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}
.err{color:#c0392b;margin-top:8px}code{word-break:break-all}
"""


def _page(lang: str, title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="robots" content="noindex"><title>{escape(title)} - Rushbase</title>'
        f"<style>{_CSS}</style></head><body><main>{body}</main></body></html>"
    )


def error_page(lang: str, message: str) -> str:
    """A terminal error (invalid client or redirect URI): shown, never redirected."""
    t = STRINGS[lang]
    return _page(lang, t["error_title"], f"<h1>{escape(t['error_title'])}</h1><p>{escape(message)}</p>")


def consent_page(
    lang: str,
    *,
    client_name: str,
    redirect_host: str,
    scopes: list[str],
    fields: dict[str, str],
    csrf: str,
    signed_in_as: str | None,
    message: str | None = None,
) -> str:
    """Login + consent form; ``fields`` are the original authorization parameters."""
    t = STRINGS[lang]
    labels = SCOPE_LABELS[lang]
    hidden = "".join(
        f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">' for k, v in fields.items()
    )
    items = "".join(f"<li>{escape(labels.get(s, s))} <code>{escape(s)}</code></li>" for s in scopes)
    if signed_in_as:
        login = f'<p class="muted">{escape(t["signed_in"])} <strong>{escape(signed_in_as)}</strong></p>'
    else:
        login = (
            f'<p class="muted">{escape(t["login_hint"])}</p>'
            f'<label>{escape(t["username"])}<input name="username" autocomplete="username" required></label>'
            f'<label>{escape(t["password"])}<input name="password" type="password" '
            'autocomplete="current-password" required></label>'
        )
    err = f'<p class="err" role="alert">{escape(message)}</p>' if message else ""
    body = (
        f"<h1>{escape(t['title'])}</h1>"
        f"<p><strong>{escape(client_name)}</strong> {escape(t['wants'])}</p>"
        f'<p class="muted">{escape(t["redirect"])} <code>{escape(redirect_host)}</code></p>'
        f"<p>{escape(t['scopes'])}:</p><ul>{items}</ul>"
        f'<form method="post" action="/oauth/authorize">{hidden}'
        f'<input type="hidden" name="csrf" value="{escape(csrf)}">{login}{err}'
        f'<div class="row"><button name="decision" value="deny" type="submit" formnovalidate>'
        f'{escape(t["deny"])}</button><button class="primary" name="decision" value="allow" '
        f'type="submit">{escape(t["allow"])}</button></div></form>'
        f'<p class="muted">{escape(t["note"])}</p>'
    )
    return _page(lang, t["title"], body)
