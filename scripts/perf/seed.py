#!/usr/bin/env python3
"""Generate a realistic synthetic catalog directly in a dedicated PostgreSQL database.

Used for the M7 scale test (spec section 16): 100 000 assets / about 50 TB, 70 % video with 3-20
shots, 20 % photos, 10 % audio, AI descriptions in German and English, transcripts for 30 % of the
videos, tags in six taxonomy fields and clustered, L2-normalised 768-d vectors (about 1 M image
vectors, about 1 M text vectors). Rows are loaded with COPY; the vectors use the binary format.

    uv run python scripts/perf/seed.py --admin-url postgresql://rushbase:perf@127.0.0.1:15433/postgres
    uv run python scripts/perf/seed.py ... --drop        # remove the database again

The database is dropped and recreated on every run, migrated to head by Alembic, and its HNSW
indexes are built after the load (incremental inserts into HNSW are far too slow for bulk loads).
"""

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from numpy.typing import NDArray
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vocab  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DIM = 768
CLUSTERS = 300
IMAGE_MODEL = "siglip2-base-patch16-256"
IMAGE_REVISION = "perf-fp16"
TAX_FIELDS: dict[str, tuple[str, str, list[vocab.Pair]]] = {
    "location": ("Ort", "Location", vocab.LOCATIONS),
    "activity": ("Aktivität", "Activity", vocab.ACTIONS),
    "mood": ("Stimmung", "Mood", vocab.MOODS),
    "subject": ("Motiv", "Subject", vocab.SUBJECTS),
    "weather": ("Licht und Wetter", "Light and weather", vocab.LIGHTS),
    "genre": ("Genre", "Genre", vocab.GENRES),
}
MEDIA_SHARE = (0.7, 0.2, 0.1)
MEAN_BYTES = {"video": 650e6, "photo": 25e6, "audio": 40e6}
TARGET_TOTAL_BYTES = 50e12
HNSW_TABLES = (
    ("embedding_image", "ix_embedding_image_hnsw"),
    ("embedding_text", "ix_embedding_text_hnsw"),
)
COPY_TRIGGER_TABLES = ("asset", "asset_file", "asset_tag", "transcript_segment")


def log(message: str) -> None:
    """Progress output with elapsed wall time."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


@dataclass
class SeedStats:
    """What a run produced (printed and returned for the report)."""

    counts: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    total_bytes: int = 0
    database_bytes: int = 0
    table_bytes: dict[str, int] = field(default_factory=dict)


def slug(text: str) -> str:
    """Taxonomy value key of an English label."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def new_ids(rng: np.random.Generator, count: int) -> NDArray[np.void]:
    """``count`` random UUIDs as raw 16-byte void items."""
    return np.frombuffer(rng.bytes(16 * count), dtype="V16").copy()


def uuid_str(raw: Any) -> str:
    """Canonical text form of a 16-byte id."""
    return str(uuid.UUID(bytes=bytes(raw)))


def unit_vectors(
    rng: np.random.Generator, centers: NDArray[np.float32], clusters: NDArray[np.int64]
) -> NDArray[np.float32]:
    """Vectors around their cluster centres (cosine to the centre about 0.7), L2-normalised."""
    noise = rng.standard_normal((len(clusters), DIM), dtype=np.float32) / math.sqrt(DIM)
    vectors = centers[clusters] + 0.95 * noise
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors.astype(np.float32)


def binary_copy(columns: Sequence[tuple[str, NDArray[Any]]], vectors: NDArray[np.float32]) -> bytes:
    """One chunk of PostgreSQL ``COPY ... (FORMAT BINARY)`` rows ending in a ``vector`` column.

    ``columns`` are fixed-width arrays (``V16`` ids, ``S<n>`` text, ``>i8`` bigint); every row has
    the same widths, so the whole chunk is a single structured numpy array.
    """
    layout: list[tuple[str, str] | tuple[str, str, tuple[int]]] = [("nf", ">i2")]
    for name, array in columns:
        layout.append((f"{name}_len", ">i4"))
        layout.append(
            (name, array.dtype.str if array.dtype.kind == "i" else f"V{array.dtype.itemsize}")
        )
    layout += [("v_len", ">i4"), ("v_dim", ">i2"), ("v_unused", ">i2"), ("v", ">f4", (DIM,))]
    out = np.zeros(len(vectors), dtype=np.dtype(layout))
    out["nf"] = len(columns) + 1
    for name, array in columns:
        out[f"{name}_len"] = array.dtype.itemsize
        out[name] = array.view(out.dtype[name]) if array.dtype.kind != "i" else array
    out["v_len"] = 4 + 4 * DIM
    out["v_dim"] = DIM
    out["v"] = vectors
    return out.tobytes()


BINARY_HEADER = b"PGCOPY\n\xff\r\n\x00" + (0).to_bytes(4, "big") + (0).to_bytes(4, "big")
BINARY_TRAILER = (-1).to_bytes(2, "big", signed=True)


def text_const(value: str, count: int) -> NDArray[np.bytes_]:
    """A column of ``count`` identical short strings."""
    raw = value.encode()
    return np.full(count, raw, dtype=f"S{len(raw)}")


def pick(py: random.Random, pairs: Sequence[vocab.Pair]) -> vocab.Pair:
    return pairs[py.randrange(len(pairs))]


def weighted(
    rng: np.random.Generator, options: Sequence[Any], weights: Sequence[float], n: int
) -> Any:
    p = np.asarray(weights, dtype=float)
    return rng.choice(len(options), size=n, p=p / p.sum())


class Describer:
    """Varied bilingual scene descriptions from the vocabulary."""

    def __init__(self, py: random.Random) -> None:
        self.py = py

    def scene(self) -> dict[str, str]:
        subject, action = pick(self.py, vocab.SUBJECTS), pick(self.py, vocab.ACTIONS)
        place, light = pick(self.py, vocab.LOCATIONS), pick(self.py, vocab.LIGHTS)
        mood, motion = pick(self.py, vocab.MOODS), self.py.choice(vocab.MOTIONS)
        short_de = f"{subject[0].capitalize()} {action[0]} {place[0]}"
        short_en = f"{subject[1].capitalize()} {action[1]} {place[1]}"
        return {
            "short_de": short_de,
            "short_en": short_en,
            "long_de": f"{short_de}, {light[0]}. Die Stimmung ist {mood[0]}. Kamera: {motion}.",
            "long_en": f"{short_en}, {light[1]}. The mood is {mood[1]}. Camera: {motion}.",
            "motion": motion,
        }

    def speech(self, lang: str) -> str:
        fillers = vocab.FILLERS_DE if lang == "de" else vocab.FILLERS_EN
        scene = self.scene()
        parts = [self.py.choice(fillers), scene["short_de" if lang == "de" else "short_en"] + "."]
        self.py.shuffle(parts)
        return " ".join(parts)


@dataclass
class Plan:
    """Per-asset facts shared between the table loaders."""

    media: list[str]
    ids: NDArray[np.void]
    file_ids: NDArray[np.void]
    batch_ids: NDArray[np.void]
    batch_of: NDArray[np.int64]
    cluster: NDArray[np.int64]
    shot_counts: NDArray[np.int64]
    sizes: NDArray[np.int64]
    capture: list[datetime]
    transcript: NDArray[np.bool_]
    tag_values: dict[str, list[tuple[Any, str, str]]] = field(default_factory=dict)


def connect(url: str, **kwargs: Any) -> psycopg.Connection[Any]:
    return psycopg.connect(url, **kwargs)


def recreate_database(admin_url: str, name: str) -> None:
    with connect(admin_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def database_url(admin_url: str, name: str) -> str:
    return make_url(admin_url).set(database=name).render_as_string(hide_password=False)


def migrate(url: str) -> None:
    """Run Alembic to head against ``url``."""
    env = {
        **os.environ,
        "RUSHBASE_DATABASE_URL": make_url(url)
        .set(drivername="postgresql+psycopg")
        .render_as_string(hide_password=False),
        "RUSHBASE_SECRET_KEY": "p" * 40,
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "server" / "alembic.ini"),
            "upgrade",
            "head",
        ],
        check=True,
        env=env,
        cwd=ROOT,
    )


def encoder_identity() -> tuple[str, str]:
    """Model id and revision the server's e5 encoder expects (from the worker lock file)."""
    from rushbase_server.search.encoder import load_layout
    from rushbase_server.settings import Settings

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key=SecretStr("p" * 40),
        database_url=SecretStr("postgresql+psycopg://unused"),
    )
    model_id, revision, _files = load_layout(settings)["text"]
    return model_id, revision or "perf"


def copy_rows(
    conn: psycopg.Connection[Any], table: str, columns: Sequence[str], rows: Iterator[Sequence[Any]]
) -> int:
    """COPY ``rows`` (text format) into ``table`` and return how many were sent."""
    count = 0
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with conn.cursor().copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def copy_binary(
    conn: psycopg.Connection[Any], table: str, columns: Sequence[str], chunks: Iterator[bytes]
) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT BINARY)").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with conn.cursor().copy(statement) as copy:
        copy.write(BINARY_HEADER)
        for chunk in chunks:
            copy.write(chunk)
        copy.write(BINARY_TRAILER)


def build_plan(rng: np.random.Generator, n: int, batches: int) -> Plan:
    kinds = ["video", "photo", "audio"]
    media = [kinds[i] for i in rng.choice(3, size=n, p=MEDIA_SHARE)]
    raw = np.array([rng.lognormal(math.log(MEAN_BYTES[m]) - 0.4, 0.9) for m in media])
    sizes = (raw * TARGET_TOTAL_BYTES / raw.sum()).astype(np.int64)
    start = datetime(2018, 1, 1, tzinfo=UTC)
    batch_days = np.sort(rng.integers(0, 8 * 365 + 280, size=batches))
    batch_of = rng.integers(0, batches, size=n)
    capture = [
        start + timedelta(days=int(batch_days[b]), seconds=int(rng.integers(6 * 3600, 20 * 3600)))
        for b in batch_of
    ]
    shots = np.where(np.array(media) == "video", rng.integers(3, 21, size=n), 0).astype(np.int64)
    return Plan(
        media=media,
        ids=new_ids(rng, n),
        file_ids=new_ids(rng, n),
        batch_ids=new_ids(rng, batches),
        batch_of=batch_of,
        cluster=rng.integers(0, CLUSTERS, size=n),
        shot_counts=shots,
        sizes=sizes,
        capture=capture,
        transcript=(np.array(media) == "video") & (rng.random(n) < 0.3),
    )


def load_taxonomy(conn: psycopg.Connection[Any], plan: Plan, rng: np.random.Generator) -> None:
    field_ids = {key: uuid.UUID(bytes=bytes(new_ids(rng, 1)[0])) for key in TAX_FIELDS}
    copy_rows(
        conn,
        "tax_field",
        ["id", "key", "label_de", "label_en", "kind", "sort"],
        iter(
            [
                (field_ids[k], k, de, en, "multi" if k in ("subject", "activity") else "single", i)
                for i, (k, (de, en, _v)) in enumerate(TAX_FIELDS.items())
            ]
        ),
    )
    values: list[tuple[Any, ...]] = []
    for key, (_de, _en, pairs) in TAX_FIELDS.items():
        plan.tag_values[key] = []
        for de, en in pairs:
            value_id = uuid.UUID(bytes=bytes(new_ids(rng, 1)[0]))
            values.append((value_id, field_ids[key], slug(en), de, en, [], "approved"))
            plan.tag_values[key].append((value_id, de, en))
    copy_rows(
        conn,
        "tax_value",
        ["id", "field_id", "value", "label_de", "label_en", "synonyms", "status"],
        iter(values),
    )
    plan.tag_values["_field_ids"] = [(fid, k, k) for k, fid in field_ids.items()]


def load_batches(conn: psycopg.Connection[Any], plan: Plan) -> None:
    days: dict[int, datetime] = {}
    for index, batch in enumerate(plan.batch_of):
        days.setdefault(int(batch), plan.capture[index])

    def rows() -> Iterator[tuple[Any, ...]]:
        for i, raw in enumerate(plan.batch_ids):
            day = days.get(i, datetime(2020, 1, 1, tzinfo=UTC)).date()
            label = f"{day.isoformat()} {vocab.LOCATIONS[i % len(vocab.LOCATIONS)][1]}"
            yield (
                uuid_str(raw), f"Input/{day.isoformat()}-{i:04d}", label, day, "done",
                datetime.combine(day, datetime.min.time(), UTC),
            )  # fmt: skip

    copy_rows(
        conn, "batch", ["id", "input_path", "label", "hint_date", "status", "first_seen_at"], rows()
    )


def metadata_blob(
    py: random.Random, camera: str, width: int, height: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plausible normalized and raw probe output (the raw part dominates the row size)."""
    normalized = {"camera_model": camera, "width": width, "height": height, "frame_rate": 25.0}
    raw: dict[str, Any] = {
        f"Tag{k:03d}": f"{camera}-{py.randrange(10**6)}-{'x' * py.randrange(20, 60)}"
        for k in range(48)
    }
    return normalized, raw


def load_assets(  # noqa: PLR0915 - one linear pass over the generated rows keeps the loader simple
    conn: psycopg.Connection[Any], plan: Plan, rng: np.random.Generator, py: random.Random
) -> None:
    """Assets, their primary files and probe metadata."""
    n = len(plan.media)
    cameras = weighted(rng, vocab.CAMERAS, [c[2] for c in vocab.CAMERAS], n)
    lenses = weighted(rng, vocab.LENSES, [1 / (i + 1) for i in range(len(vocab.LENSES))], n)
    logs = rng.integers(-3, len(vocab.LOG_PROFILES), size=n)
    describer = Describer(py)
    asset_rows: list[tuple[Any, ...]] = []
    file_rows: list[tuple[Any, ...]] = []
    meta_rows: list[tuple[Any, ...]] = []
    statuses = rng.choice(
        ["filed", "review", "processing", "trashed"], size=n, p=[0.935, 0.04, 0.02, 0.005]
    )
    for i in range(n):
        media = plan.media[i]
        asset_id, file_id = uuid_str(plan.ids[i]), uuid_str(plan.file_ids[i])
        make, model, _w = vocab.CAMERAS[int(cameras[i])]
        status = str(statuses[i])
        captured = plan.capture[i]
        if media == "video":
            width, height, _w2 = vocab.VIDEO_RESOLUTIONS[
                int(
                    weighted(
                        rng, vocab.VIDEO_RESOLUTIONS, [r[2] for r in vocab.VIDEO_RESOLUTIONS], 1
                    )[0]
                )
            ]
            duration = int(min(max(rng.lognormal(math.log(25_000), 1.0), 2_000), 1_800_000))
            codec = vocab.VIDEO_CODECS[py.randrange(5)]
            ext, fps = (
                ("mov" if codec in ("prores", "hevc") else "mp4"),
                py.choice([23.976, 25, 25, 29.97, 50, 59.94]),
            )
        elif media == "photo":
            width, height, _w2 = vocab.PHOTO_RESOLUTIONS[
                int(
                    weighted(
                        rng, vocab.PHOTO_RESOLUTIONS, [r[2] for r in vocab.PHOTO_RESOLUTIONS], 1
                    )[0]
                )
            ]
            duration, codec, ext, fps = None, py.choice(["jpeg", "jpeg", "raw"]), "jpg", None
        else:
            width = height = None
            duration, codec, ext, fps = (
                int(rng.lognormal(math.log(90_000), 0.8)),
                "aac",
                "wav",
                None,
            )
        orientation = (
            None
            if width is None or height is None
            else ("portrait" if height > width else "landscape" if width > height else "square")
        )
        log_profile = None if media != "video" or logs[i] < 0 else vocab.LOG_PROFILES[int(logs[i])]
        lens = vocab.LENSES[int(lenses[i])] if media != "audio" else None
        scene = describer.scene()
        analysed = media != "audio" and status in ("filed", "review")
        directory = f"Content/{captured.year}/{captured.date().isoformat()} {vocab.LOCATIONS[int(plan.batch_of[i]) % 25][1]}"
        rel_path = f"{directory}/{make[:3].upper()}_{i:06d}.{ext}"
        pending = status == "review"
        derived = {"files": [
            {"kind": "poster", "rel_path": f"derived/{asset_id}/poster.jpg"},
            {"kind": "sprite", "rel_path": f"derived/{asset_id}/sprite.jpg", "tiles": 40},
            {"kind": "proxy", "rel_path": f"derived/{asset_id}/proxy.mp4"},
        ]} if media != "audio" else {"files": [{"kind": "waveform", "rel_path": f"derived/{asset_id}/wave.json"}]}  # fmt: skip
        normalized, raw = metadata_blob(py, model, width or 0, height or 0)
        asset_rows.append((
            asset_id, uuid_str(plan.batch_ids[plan.batch_of[i]]), media, status, file_id, directory,
            captured, duration, width, height, fps, codec, make, model, lens,
            py.choice([100, 200, 400, 800, 1600, 3200]) if media != "audio" else None, log_profile,
            orientation, int(plan.sizes[i]), Jsonb(derived),
            f"Video {model}, {width}x{height}, {codec}" if width else f"Audio {codec}",
            f"{model}, {width}x{height}, {codec}" if width else f"Audio, {codec}",
            scene["short_de"] if analysed else None,
            scene["long_de"] if analysed else None, scene["long_en"] if analysed else None,
            scene["short_en"] if analysed else None,
            py.choice(vocab.SHOT_SIZES) if analysed else None,
            py.choice(vocab.ANGLES) if analysed else None,
            py.choice(vocab.INT_EXT) if analysed else None,
            py.choice(vocab.TIMES) if analysed else None,
            py.choice([0, 1, 2, 3]) if analysed else None,
            "pending" if pending else py.choice(["none", "none", "done"]),
            ["low_confidence"] if pending else [],
            py.choice(["yes", "no", "unknown"]), py.choice(["yes", "no", "unknown"]),
            captured + timedelta(days=1), captured + timedelta(days=1), captured + timedelta(days=2),
            None if py.random() > 0.3 else py.choice(["Hafenfest", "Interview Bäckerei", "Drohnenflug Küste", "Sommerdreh"]),
            py.choice([None, None, 3, 4, 5]),
        ))  # fmt: skip
        file_rows.append(
            (file_id, asset_id, "primary", rel_path, rel_path, int(plan.sizes[i]), captured)
        )
        meta_rows.append((asset_id, Jsonb(normalized), Jsonb(raw)))
    columns = [
        "id", "batch_id", "media_type", "status", "primary_file_id", "content_rel_dir", "capture_at",
        "duration_ms", "width", "height", "fps", "codec", "camera_make", "camera_model", "lens_model",
        "iso", "log_profile", "orientation", "size_total", "derived", "tech_summary_de",
        "tech_summary_en", "summary_short", "summary_long_de", "summary_long_en", "summary_short_en",
        "shot_size", "camera_angle", "interior_exterior", "time_of_day", "people_count",
        "review_state", "review_reasons", "person_identifiable", "consent", "filed_at",
        "created_at", "updated_at", "title", "rating",
    ]  # fmt: skip
    copy_rows(conn, "asset", columns, iter(asset_rows))
    copy_rows(
        conn,
        "asset_file",
        ["id", "asset_id", "role", "rel_path", "original_rel_path", "size", "mtime"],
        iter(file_rows),
    )
    copy_rows(conn, "asset_metadata", ["asset_id", "normalized", "raw"], iter(meta_rows))


def load_shots(
    conn: psycopg.Connection[Any], plan: Plan, rng: np.random.Generator, py: random.Random
) -> tuple[NDArray[np.void], NDArray[np.int64]]:
    """Shots of every video; returns the shot ids and the asset index of every shot."""
    describer = Describer(py)
    total = int(plan.shot_counts.sum())
    shot_ids = new_ids(rng, total)
    shot_asset = np.repeat(np.arange(len(plan.media)), plan.shot_counts)

    def rows() -> Iterator[tuple[Any, ...]]:
        position = 0
        for i in np.flatnonzero(plan.shot_counts):
            asset_id = uuid_str(plan.ids[i])
            count = int(plan.shot_counts[i])
            cuts = np.sort(rng.integers(1_000, 120_000, size=count))
            start = 0
            for idx in range(count):
                end = int(cuts[idx]) + start
                scene = describer.scene()
                key = uuid_str(shot_ids[position])
                frames = [{"t_ms": (start + end) // 2, "position": "middle",
                           "rel_path": f"derived/{asset_id}/kf_{idx}_mid.jpg"}]  # fmt: skip
                yield (
                    key, asset_id, idx, start, end, Jsonb(frames), scene["motion"],
                    float(rng.random() * 800), float(rng.random()), float(rng.random()), float(rng.random()),
                    scene["short_en"], scene["long_de"], scene["long_en"], py.choice(vocab.SHOT_SIZES),
                    py.choice(vocab.ANGLES), scene["short_de"], scene["short_en"],
                    py.choice(vocab.INT_EXT), py.choice(vocab.TIMES), py.choice([0, 1, 2]),
                )  # fmt: skip
                position += 1
                start = end

    copy_rows(
        conn,
        "shot",
        [
            "id", "asset_id", "idx", "start_ms", "end_ms", "keyframes", "camera_motion", "sharpness",
            "shake", "exposure", "noise", "description_short", "description_long_de",
            "description_long_en", "shot_size", "angle", "description_short_de",
            "description_short_en", "interior_exterior", "time_of_day", "people_count",
        ],
        rows(),
    )  # fmt: skip
    return shot_ids, shot_asset


def load_tags(
    conn: psycopg.Connection[Any], plan: Plan, rng: np.random.Generator, py: random.Random
) -> int:
    field_ids = {k: i for i, k, _ in plan.tag_values["_field_ids"]}
    fields = {k: v for k, v in plan.tag_values.items() if not k.startswith("_")}
    per_field = {
        "location": (1, 1),
        "activity": (1, 2),
        "mood": (1, 1),
        "subject": (1, 3),
        "weather": (0, 1),
        "genre": (1, 1),
    }

    def rows() -> Iterator[tuple[Any, ...]]:
        for i, media in enumerate(plan.media):
            if media == "audio" and py.random() < 0.5:
                continue
            asset_id = uuid_str(plan.ids[i])
            for key, values in fields.items():
                low, high = per_field[key]
                chosen = py.sample(range(len(values)), k=min(py.randint(low, high), len(values)))
                for index in chosen:
                    source = py.choices(["ai", "human", "claude"], [0.85, 0.1, 0.05])[0]
                    yield (
                        str(uuid.uuid4()), asset_id, field_ids[key], values[index][0],
                        source, round(py.uniform(0.5, 0.99), 3) if source == "ai" else None,
                    )  # fmt: skip

    return copy_rows(
        conn,
        "asset_tag",
        ["id", "asset_id", "field_id", "value_id", "source", "confidence"],
        rows(),
    )


def load_transcripts(
    conn: psycopg.Connection[Any], plan: Plan, rng: np.random.Generator, py: random.Random
) -> list[tuple[int, int, int]]:
    """Segments for the transcribed videos; returns ``(asset index, start_ms, end_ms)`` chunks."""
    describer = Describer(py)
    chunks: list[tuple[int, int, int]] = []

    def rows() -> Iterator[tuple[Any, ...]]:
        for i in np.flatnonzero(plan.transcript):
            asset_id = uuid_str(plan.ids[i])
            lang = "de" if py.random() < 0.6 else "en"
            clock = 0
            segments = py.randint(12, 38)
            for idx in range(segments):
                text = describer.speech(lang)
                words = text.split()
                step = 380
                length = len(words) * step
                word_json = [
                    {"w": w, "start": clock + k * step, "end": clock + (k + 1) * step}
                    for k, w in enumerate(words)
                ]
                yield (
                    str(uuid.uuid4()),
                    asset_id,
                    idx,
                    clock,
                    clock + length,
                    text,
                    lang,
                    Jsonb(word_json),
                )
                if idx % 3 == 0:
                    chunks.append((int(i), clock, clock + 3 * length))
                clock += length + py.randint(200, 3000)

    copy_rows(
        conn, "transcript_segment",
        ["id", "asset_id", "idx", "start_ms", "end_ms", "text", "language", "words"], rows(),
    )  # fmt: skip
    return chunks


def load_audio_events(conn: psycopg.Connection[Any], plan: Plan, py: random.Random) -> int:
    def rows() -> Iterator[tuple[Any, ...]]:
        for i, media in enumerate(plan.media):
            if media == "photo" or (media == "video" and py.random() > 0.1):
                continue
            for _ in range(py.randint(1, 4)):
                start = py.randint(0, 60_000)
                yield (
                    str(uuid.uuid4()), uuid_str(plan.ids[i]),
                    py.choice(vocab.AUDIO_EVENTS), start, start + py.randint(300, 6000),
                    round(py.uniform(0.4, 0.99), 3),
                )  # fmt: skip

    return copy_rows(
        conn, "audio_event", ["id", "asset_id", "tag", "start_ms", "end_ms", "confidence"], rows()
    )


def load_vectors(  # noqa: PLR0913
    conn: psycopg.Connection[Any],
    plan: Plan,
    rng: np.random.Generator,
    shot_ids: NDArray[np.void],
    shot_asset: NDArray[np.int64],
    chunks: list[tuple[int, int, int]],
    text_model: tuple[str, str],
    batch: int = 40_000,
) -> dict[str, int]:
    """Image vectors per keyframe and text vectors per summary, shot and transcript chunk."""
    image_centers = unit_vectors(
        rng, rng.standard_normal((CLUSTERS, DIM), dtype=np.float32), np.arange(CLUSTERS)
    )
    text_centers = unit_vectors(
        rng, rng.standard_normal((CLUSTERS, DIM), dtype=np.float32), np.arange(CLUSTERS)
    )
    counts = {"embedding_image": 0, "embedding_text": 0}
    shot_cluster = np.where(
        rng.random(len(shot_asset)) < 0.7,
        plan.cluster[shot_asset],
        rng.integers(0, CLUSTERS, size=len(shot_asset)),
    )
    image_model, text_id, text_rev = IMAGE_MODEL, text_model[0], text_model[1]

    def image_chunks() -> Iterator[bytes]:
        extra = rng.random(len(shot_asset)) < 0.25  # 25 % of the shots have 3 keyframes
        for frame, mask in (
            ("kf1", np.ones(len(shot_asset), bool)),
            ("kf0", extra),
            ("kf2", extra),
        ):
            selected = np.flatnonzero(mask)
            for lo in range(0, len(selected), batch):
                part = selected[lo : lo + batch]
                vectors = unit_vectors(rng, image_centers, shot_cluster[part])
                counts["embedding_image"] += len(part)
                yield binary_copy(
                    [("id", new_ids(rng, len(part))), ("asset", plan.ids[shot_asset[part]]),
                     ("shot", shot_ids[part]), ("kf", text_const(frame, len(part))),
                     ("model", text_const(image_model, len(part))), ("rev", text_const(IMAGE_REVISION, len(part)))],
                    vectors,
                )  # fmt: skip

    copy_binary(
        conn,
        "embedding_image",
        ["id", "asset_id", "shot_id", "keyframe", "model_id", "revision", "embedding"],
        image_chunks(),
    )
    photos = np.array([i for i, m in enumerate(plan.media) if m == "photo"], dtype=np.int64)

    def photo_chunks() -> Iterator[bytes]:
        for lo in range(0, len(photos), batch):
            part = photos[lo : lo + batch]
            counts["embedding_image"] += len(part)
            yield binary_copy(
                [("id", new_ids(rng, len(part))), ("asset", plan.ids[part]), ("kf", text_const("kf1", len(part))),
                 ("model", text_const(image_model, len(part))), ("rev", text_const(IMAGE_REVISION, len(part)))],
                unit_vectors(rng, image_centers, plan.cluster[part]),
            )  # fmt: skip

    copy_binary(
        conn,
        "embedding_image",
        ["id", "asset_id", "keyframe", "model_id", "revision", "embedding"],
        photo_chunks(),
    )

    def text_stream(kind: str, asset_index: NDArray[np.int64], shots: NDArray[np.void] | None, clusters: NDArray[np.int64],
                    spans: NDArray[np.int64] | None = None) -> Iterator[bytes]:  # fmt: skip
        for lo in range(0, len(asset_index), batch):
            part = slice(lo, lo + batch)
            size = len(asset_index[part])
            columns: list[tuple[str, NDArray[Any]]] = [
                ("id", new_ids(rng, size)),
                ("asset", plan.ids[asset_index[part]]),
            ]
            if shots is not None:
                columns.append(("shot", shots[part]))
            columns += [
                ("kind", text_const(kind, size)),
                ("model", text_const(text_id, size)),
                ("rev", text_const(text_rev, size)),
            ]
            if spans is not None:
                columns += [
                    ("start", spans[part, 0].astype(">i8")),
                    ("end", spans[part, 1].astype(">i8")),
                ]
            counts["embedding_text"] += size
            yield binary_copy(columns, unit_vectors(rng, text_centers, clusters[part]))

    base = ["id", "asset_id"]
    tail = ["kind", "model_id", "revision"]
    everyone = np.arange(len(plan.media), dtype=np.int64)
    copy_binary(
        conn,
        "embedding_text",
        [*base, *tail, "embedding"],
        text_stream("asset_summary", everyone, None, plan.cluster),
    )
    copy_binary(
        conn,
        "embedding_text",
        [*base, "shot_id", *tail, "embedding"],
        text_stream("shot_description", shot_asset, shot_ids, shot_cluster),
    )
    if chunks:
        spans = np.array([(s, e) for _a, s, e in chunks], dtype=np.int64)
        owners = np.array([a for a, _s, _e in chunks], dtype=np.int64)
        copy_binary(
            conn,
            "embedding_text",
            [*base, *tail, "start_ms", "end_ms", "embedding"],
            text_stream("transcript_chunk", owners, None, plan.cluster[owners], spans),
        )
    return counts


def load_needs(conn: psycopg.Connection[Any], py: random.Random) -> int:
    """Six needs lists with eight wanted shots each (auto-matching input)."""
    describer = Describer(py)
    items = 0
    for number in range(6):
        list_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO need_list (id, title, purpose, created_by_kind) VALUES (%s, %s, %s, 'user')",
            (list_id, f"Perf list {number}", "synthetic needs list"),
        )
        for position in range(8):
            filters = py.choice(
                [{}, {"media_type": "video"}, {"shot_size": "wide"}, {"time_of_day": "night"}]
            )
            conn.execute(
                "INSERT INTO need_item (id, list_id, position, description, filters, count_wanted) VALUES (%s, %s, %s, %s, %s, 2)",
                (uuid.uuid4(), list_id, position, describer.scene()["short_en"], Jsonb(filters)),
            )
            items += 1
    return items


def build_indexes(admin: psycopg.Connection[Any], maintenance_mem: str, params: str) -> None:
    admin.execute(f"SET maintenance_work_mem = '{maintenance_mem}'")  # noqa: S608
    admin.execute("SET max_parallel_maintenance_workers = 3")
    for table, index in HNSW_TABLES:
        started = time.monotonic()
        admin.execute(f"DROP INDEX IF EXISTS {index}")  # noqa: S608
        admin.execute(
            f"CREATE INDEX {index} ON {table} USING hnsw (embedding vector_cosine_ops){params}"
        )  # noqa: S608
        log(f"built {index} in {time.monotonic() - started:.0f} s")


def finish(
    conn: psycopg.Connection[Any], stats: SeedStats, maintenance_mem: str, hnsw_params: str
) -> None:
    """Build the HNSW indexes, vacuum and collect row counts and sizes."""
    build_indexes(conn, maintenance_mem, hnsw_params)
    conn.execute("VACUUM (ANALYZE)")
    for table in (
        "asset",
        "shot",
        "asset_tag",
        "transcript_segment",
        "embedding_image",
        "embedding_text",
        "asset_file",
        "asset_metadata",
    ):
        size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (table,)).fetchone()
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        stats.table_bytes[table] = int(size[0]) if size else 0
        stats.counts[table] = int(count[0]) if count else 0
    size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
    stats.database_bytes = int(size[0]) if size else 0


def seed(  # noqa: PLR0913
    admin_url: str,
    db: str,
    assets: int = 100_000,
    rng_seed: int = 7,
    maintenance_mem: str = "3GB",
    hnsw_params: str = "",
    build_hnsw: bool = True,
) -> SeedStats:
    """Recreate ``db`` and fill it; returns row counts, timings and sizes."""
    stats = SeedStats()
    started = time.monotonic()
    rng, py = np.random.default_rng(rng_seed), random.Random(rng_seed)
    recreate_database(admin_url, db)
    url = database_url(admin_url, db)
    migrate(url)
    text_model = encoder_identity()
    batches = max(4, assets // 250)
    plan = build_plan(rng, assets, batches)
    stats.total_bytes = int(plan.sizes.sum())
    with connect(url, autocommit=True) as conn:
        conn.execute("SET synchronous_commit = off")
        for table in COPY_TRIGGER_TABLES:
            conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")  # noqa: S608
        for _table, index in HNSW_TABLES:
            conn.execute(f"DROP INDEX IF EXISTS {index}")  # noqa: S608
        steps = time.monotonic()
        load_taxonomy(conn, plan, rng)
        load_batches(conn, plan)
        load_assets(conn, plan, rng, py)
        log(f"assets, files, metadata loaded ({time.monotonic() - steps:.0f} s)")
        shot_ids, shot_asset = load_shots(conn, plan, rng, py)
        log(f"{len(shot_ids)} shots loaded")
        stats.counts["asset_tag"] = load_tags(conn, plan, rng, py)
        chunks = load_transcripts(conn, plan, rng, py)
        stats.counts["audio_event"] = load_audio_events(conn, plan, py)
        stats.counts["need_item"] = load_needs(conn, py)
        log("tags, transcripts, audio events, needs loaded")
        stats.counts.update(load_vectors(conn, plan, rng, shot_ids, shot_asset, chunks, text_model))
        log(
            f"{stats.counts['embedding_image']} image / {stats.counts['embedding_text']} text vectors loaded"
        )
        for table in COPY_TRIGGER_TABLES:
            conn.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")  # noqa: S608
        conn.execute("UPDATE asset a SET search_tsv = asset_search_vector(a)")
        log("search vectors computed")
        if build_hnsw:
            finish(conn, stats, maintenance_mem, hnsw_params)
    stats.seconds["total"] = time.monotonic() - started
    return stats


def finish_only(admin_url: str, db: str, maintenance_mem: str, hnsw_params: str) -> SeedStats:
    """Resume after a failed index build: build indexes, vacuum, collect sizes."""
    stats = SeedStats()
    started = time.monotonic()
    with connect(database_url(admin_url, db), autocommit=True) as conn:
        finish(conn, stats, maintenance_mem, hnsw_params)
    stats.seconds["finish"] = time.monotonic() - started
    return stats


def drop_database(admin_url: str, db: str) -> None:
    with connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--admin-url", required=True, help="libpq URL of a maintenance database (postgres)"
    )
    parser.add_argument("--db", default="rushbase_perf")
    parser.add_argument("--assets", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--maintenance-mem", default="3GB", help="maintenance_work_mem for the HNSW builds"
    )
    parser.add_argument(
        "--hnsw-params", default="", help='e.g. " WITH (m = 16, ef_construction = 64)"'
    )
    parser.add_argument(
        "--no-hnsw", action="store_true", help="skip the HNSW builds (build them by hand)"
    )
    parser.add_argument(
        "--finish-only", action="store_true", help="only build indexes and collect sizes"
    )
    parser.add_argument("--drop", action="store_true", help="drop the database and exit")
    args = parser.parse_args()
    if args.drop:
        drop_database(args.admin_url, args.db)
        log(f"dropped {args.db}")
        return
    if args.finish_only:
        stats = finish_only(args.admin_url, args.db, args.maintenance_mem, args.hnsw_params)
    else:
        stats = seed(
            args.admin_url,
            args.db,
            args.assets,
            args.seed,
            args.maintenance_mem,
            args.hnsw_params,
            not args.no_hnsw,
        )
    print(json.dumps({
        "counts": stats.counts, "seconds": stats.seconds, "total_bytes": stats.total_bytes,
        "database_bytes": stats.database_bytes, "table_bytes": stats.table_bytes,
    }, indent=2))  # fmt: skip


if __name__ == "__main__":
    main()
