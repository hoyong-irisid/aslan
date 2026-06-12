"""SQLite store for registered partners and OTP challenges."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import get_settings

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_ALPHABET = "".join(c for c in _CODE_ALPHABET if c not in "0O1IL")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def partner_db_path() -> Path:
    s = get_settings()
    raw = (s.partner_db_path or "data/partners.db").strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        root = Path(__file__).resolve().parents[1]
        p = (root / p).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _conn():
    db = partner_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                company TEXT NOT NULL,
                phone TEXT,
                region_key TEXT NOT NULL,
                country_iso TEXT,
                code TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'signup',
                verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_partners_region ON partners(region_key);
            CREATE INDEX IF NOT EXISTS idx_partners_active ON partners(active);
            CREATE INDEX IF NOT EXISTS idx_partners_code ON partners(code);

            CREATE TABLE IF NOT EXISTS otp_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                otp_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_challenges(email);
            """
        )
        cols = {row[1] for row in con.execute("PRAGMA table_info(partners)")}
        if "country" not in cols:
            con.execute("ALTER TABLE partners ADD COLUMN country TEXT")


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise ValueError("Invalid email address")
    return e


def generate_partner_code(length: int = 6) -> str:
    for _ in range(40):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
        if not is_active_code(code):
            return code
    raise RuntimeError("Could not allocate unique partner code")


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def is_active_code(code: str) -> bool:
    c = (code or "").strip().upper()
    if not c:
        return False
    with _conn() as con:
        row = con.execute(
            f"SELECT 1 FROM partners WHERE code = ? AND {_is_active_sql()} LIMIT 1",
            (c,),
        ).fetchone()
    return row is not None


def count_active_partners() -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM partners WHERE active = 1"
        ).fetchone()
    return int(row["n"]) if row else 0


def count_all_partners() -> int:
    with _conn() as con:
        row = con.execute("SELECT COUNT(*) AS n FROM partners").fetchone()
    return int(row["n"]) if row else 0


def _normalize_active(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("0", "false", "no", "off", ""):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
    return bool(value)


def _active_int(value: object) -> int:
    return 1 if _normalize_active(value) else 0


def row_to_partner(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "company": row["company"],
        "phone": row["phone"],
        "region_key": row["region_key"],
        "country_iso": row["country_iso"],
        "country": row["country"] if "country" in row.keys() else None,
        "code": row["code"],
        "active": _normalize_active(row["active"]),
        "source": row["source"],
        "verified_at": row["verified_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_partner_by_email(email: str) -> dict[str, Any] | None:
    e = normalize_email(email)
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM partners WHERE email = ? LIMIT 1", (e,)
        ).fetchone()
    return row_to_partner(row) if row else None


def get_partner(partner_id: int) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM partners WHERE id = ? LIMIT 1", (partner_id,)
        ).fetchone()
    return row_to_partner(row) if row else None


def _is_active_sql() -> str:
    """Treat any non-zero active flag as active (SQLite bool quirks)."""
    return "COALESCE(active, 0) != 0"


def list_partners(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM partners"
    if not include_inactive:
        q += f" WHERE {_is_active_sql()}"
    q += " ORDER BY id, region_key, company, name"
    with _conn() as con:
        rows = con.execute(q).fetchall()
    return [row_to_partner(r) for r in rows]


def admin_stats() -> dict[str, Any]:
    active_sql = _is_active_sql()
    with _conn() as con:
        active = con.execute(
            f"SELECT COUNT(*) AS n FROM partners WHERE {active_sql}"
        ).fetchone()["n"]
        total = con.execute("SELECT COUNT(*) AS n FROM partners").fetchone()["n"]
        by_region = con.execute(
            f"""
            SELECT region_key, COUNT(*) AS n
            FROM partners WHERE {active_sql}
            GROUP BY region_key ORDER BY region_key
            """
        ).fetchall()
        signup = con.execute(
            f"SELECT COUNT(*) AS n FROM partners WHERE source = 'signup' AND {active_sql}"
        ).fetchone()["n"]
        admin_added = con.execute(
            f"SELECT COUNT(*) AS n FROM partners WHERE source = 'admin' AND {active_sql}"
        ).fetchone()["n"]
    return {
        "active_partners": int(active),
        "total_partners_ever": int(total),
        "codes_issued_active": int(active),
        "signups": int(signup),
        "admin_created": int(admin_added),
        "by_region": {r["region_key"]: int(r["n"]) for r in by_region},
    }


def admin_dashboard_data(*, include_inactive: bool = True) -> dict[str, Any]:
    """Stats + partner list in one DB read (used by POST dashboard; avoids stale GET caches)."""
    active_sql = _is_active_sql()
    with _conn() as con:
        total = int(con.execute("SELECT COUNT(*) AS n FROM partners").fetchone()["n"])
        active = int(
            con.execute(f"SELECT COUNT(*) AS n FROM partners WHERE {active_sql}").fetchone()["n"]
        )
        by_region_rows = con.execute(
            f"""
            SELECT region_key, COUNT(*) AS n
            FROM partners WHERE {active_sql}
            GROUP BY region_key ORDER BY region_key
            """
        ).fetchall()
        signup = int(
            con.execute(
                f"SELECT COUNT(*) AS n FROM partners WHERE source = 'signup' AND {active_sql}"
            ).fetchone()["n"]
        )
        admin_added = int(
            con.execute(
                f"SELECT COUNT(*) AS n FROM partners WHERE source = 'admin' AND {active_sql}"
            ).fetchone()["n"]
        )
        q = "SELECT * FROM partners"
        if not include_inactive:
            q += f" WHERE {active_sql}"
        q += " ORDER BY id, region_key, company, name"
        partner_rows = con.execute(q).fetchall()

    partners = [row_to_partner(r) for r in partner_rows]
    return {
        "db_path": str(partner_db_path()),
        "stats": {
            "active_partners": active,
            "total_partners_ever": total,
            "codes_issued_active": active,
            "signups": signup,
            "admin_created": admin_added,
            "by_region": {r["region_key"]: int(r["n"]) for r in by_region_rows},
        },
        "partners": partners,
    }


def create_otp_challenge(email: str, otp: str, payload_json: str, ttl_minutes: int) -> None:
    e = normalize_email(email)
    now = _utc_now()
    expires = datetime.now(timezone.utc).timestamp() + ttl_minutes * 60
    exp_iso = datetime.fromtimestamp(expires, tz=timezone.utc).replace(
        microsecond=0
    ).isoformat()
    with _conn() as con:
        con.execute("DELETE FROM otp_challenges WHERE email = ?", (e,))
        con.execute(
            """
            INSERT INTO otp_challenges (email, otp_hash, payload_json, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (e, hash_otp(otp), payload_json, exp_iso, now),
        )


def consume_otp(email: str, otp: str) -> str | None:
    """Returns signup payload JSON if OTP valid."""
    e = normalize_email(email)
    now = datetime.now(timezone.utc)
    with _conn() as con:
        row = con.execute(
            """
            SELECT * FROM otp_challenges
            WHERE email = ? AND consumed_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (e,),
        ).fetchone()
        if not row:
            return None
        exp = datetime.fromisoformat(row["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            return None
        if row["otp_hash"] != hash_otp(otp.strip()):
            return None
        con.execute(
            "UPDATE otp_challenges SET consumed_at = ? WHERE id = ?",
            (_utc_now(), row["id"]),
        )
        return str(row["payload_json"])


def insert_partner(
    *,
    email: str,
    name: str,
    company: str,
    phone: str | None,
    region_key: str,
    country_iso: str | None,
    country: str | None = None,
    code: str | None = None,
    source: str = "signup",
) -> dict[str, Any]:
    e = normalize_email(email)
    existing = get_partner_by_email(e)
    if existing and existing["active"]:
        raise ValueError(
            f"This email is already registered (partner id {existing['id']}, "
            f"code {existing['code']}). Use Edit on that row instead of Add."
        )
    c = (code or generate_partner_code()).strip().upper()
    now = _utc_now()
    with _conn() as con:
        if existing and not existing["active"]:
            con.execute(
                """
                UPDATE partners SET name=?, company=?, phone=?, region_key=?,
                    country_iso=?, country=?, code=?, active=1, source=?, verified_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    name.strip(),
                    company.strip(),
                    (phone or "").strip() or None,
                    region_key.strip(),
                    (country_iso or "").strip().upper() or None,
                    (country or "").strip() or None,
                    c,
                    source,
                    now,
                    now,
                    existing["id"],
                ),
            )
            pid = existing["id"]
        else:
            cur = con.execute(
                """
                INSERT INTO partners
                (email, name, company, phone, region_key, country_iso, country, code,
                 active, source, verified_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    e,
                    name.strip(),
                    company.strip(),
                    (phone or "").strip() or None,
                    region_key.strip(),
                    (country_iso or "").strip().upper() or None,
                    (country or "").strip() or None,
                    c,
                    source,
                    now,
                    now,
                    now,
                ),
            )
            pid = int(cur.lastrowid)
    out = get_partner(pid)
    if out is None:
        raise RuntimeError("Partner insert failed")
    return out


def update_partner(partner_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "company", "phone", "region_key", "country_iso", "country", "active", "email"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        raise ValueError("No fields to update")
    if "email" in updates:
        updates["email"] = normalize_email(str(updates["email"]))
    if "country_iso" in updates and updates["country_iso"]:
        updates["country_iso"] = str(updates["country_iso"]).strip().upper()
    if "country" in updates and updates["country"]:
        updates["country"] = str(updates["country"]).strip()
    if "active" in updates:
        updates["active"] = _active_int(updates["active"])
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [_utc_now(), partner_id]
    with _conn() as con:
        con.execute(
            f"UPDATE partners SET {sets}, updated_at = ? WHERE id = ?",
            vals,
        )
    out = get_partner(partner_id)
    if out is None:
        raise ValueError("Partner not found")
    return out


def activate_partner(partner_id: int) -> dict[str, Any]:
    with _conn() as con:
        con.execute(
            "UPDATE partners SET active = 1, updated_at = ? WHERE id = ?",
            (_utc_now(), partner_id),
        )
    out = get_partner(partner_id)
    if out is None:
        raise ValueError("Partner not found")
    return out


def deactivate_partner(partner_id: int) -> dict[str, Any]:
    with _conn() as con:
        con.execute(
            "UPDATE partners SET active = 0, updated_at = ? WHERE id = ?",
            (_utc_now(), partner_id),
        )
    out = get_partner(partner_id)
    if out is None:
        raise ValueError("Partner not found")
    return out


def delete_partner(partner_id: int) -> dict[str, Any]:
    out = get_partner(partner_id)
    if out is None:
        raise ValueError("Partner not found")
    with _conn() as con:
        con.execute("DELETE FROM partners WHERE id = ?", (partner_id,))
    return out


def regenerate_code(partner_id: int) -> dict[str, Any]:
    code = generate_partner_code()
    with _conn() as con:
        con.execute(
            "UPDATE partners SET code = ?, updated_at = ? WHERE id = ?",
            (code, _utc_now(), partner_id),
        )
    out = get_partner(partner_id)
    if out is None:
        raise ValueError("Partner not found")
    return out
