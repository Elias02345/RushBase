"""Evaluation of the latest AI results against the golden labels.

``compute_metrics`` is pure (``(field, expected, predicted)`` triples in, metrics out) so a tiny
regression test can run without models or a database. ``run_evaluation`` collects the predictions
from the latest successful ``vision`` / ``classify`` stage results (the AI's own answer, not the
possibly human-corrected columns), stores an ``eval_run`` and ``render_markdown`` writes the report.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import EvalRun, GoldenLabel, Shot, StageResult
from rushbase_server.services.golden import CORE_FIELDS

MISSING = "(none)"
Triple = tuple[str, str, str]
"""``(field, expected, predicted)``."""


def _f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def compute_metrics(triples: Iterable[Triple]) -> dict[str, Any]:
    """Per-field accuracy, per-class precision/recall/F1 (macro F1) and the confusion matrix."""
    by_field: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for field, expected, predicted in triples:
        by_field[field].append((expected, predicted))
    fields: dict[str, Any] = {}
    correct_total = 0
    count_total = 0
    for field, pairs in sorted(by_field.items()):
        confusion: dict[str, Counter[str]] = defaultdict(Counter)
        for expected, predicted in pairs:
            confusion[expected][predicted] += 1
        classes = sorted({e for e, _ in pairs})
        per_class: dict[str, Any] = {}
        for cls in classes:
            tp = confusion[cls][cls]
            predicted_total = sum(1 for _, p in pairs if p == cls)
            expected_total = sum(1 for e, _ in pairs if e == cls)
            precision = tp / predicted_total if predicted_total else 0.0
            recall = tp / expected_total if expected_total else 0.0
            per_class[cls] = {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(_f1(precision, recall), 4),
                "support": expected_total,
            }
        correct = sum(1 for e, p in pairs if e == p)
        correct_total += correct
        count_total += len(pairs)
        fields[field] = {
            "n": len(pairs),
            "accuracy": round(correct / len(pairs), 4),
            "macro_f1": round(sum(c["f1"] for c in per_class.values()) / len(per_class), 4),
            "classes": per_class,
            "confusion": {e: dict(sorted(p.items())) for e, p in sorted(confusion.items())},
        }
    return {
        "overall": {
            "n": count_total,
            "accuracy": round(correct_total / count_total, 4) if count_total else None,
        },
        "fields": fields,
    }


def _text(value: Any) -> str:
    if value is None:
        return MISSING
    return str(value.get("value") if isinstance(value, dict) else value)


def predictions_from_outputs(
    vision: dict[str, Any] | None, classify: dict[str, Any] | None
) -> dict[tuple[int | None, str], str]:
    """``{(shot_idx, field): value}`` from raw stage outputs."""
    found: dict[tuple[int | None, str], str] = {}
    for description in (vision or {}).get("descriptions", []):
        for field in CORE_FIELDS:
            found[(description.get("shot_idx"), field)] = _text(description.get(field))
    for tag in (classify or {}).get("tags", []):
        found.setdefault((tag.get("shot_idx"), tag["field"]), tag["value"])
    return found


async def _latest_outputs(session: AsyncSession, stage: str) -> dict[UUID, StageResult]:
    rows = await session.execute(
        select(StageResult)
        .where(StageResult.stage == stage, StageResult.ok.is_(True))
        .order_by(StageResult.asset_id, StageResult.finished_at.desc(), StageResult.id.desc())
        .ext(distinct_on(StageResult.asset_id))
    )
    return {r.asset_id: r for r in rows.scalars()}


async def collect_triples(session: AsyncSession) -> tuple[list[Triple], dict[str, Any]]:
    """Golden labels joined with the latest AI predictions, plus the model refs that made them."""
    vision = await _latest_outputs(session, "vision")
    classify = await _latest_outputs(session, "classify")
    labels = (await session.execute(select(GoldenLabel))).scalars().all()
    shot_idx = {sid: idx for sid, idx in await session.execute(select(Shot.id, Shot.idx))}  # noqa: C416
    cache: dict[UUID, dict[tuple[int | None, str], str]] = {}
    triples: list[Triple] = []
    for label in labels:
        if label.asset_id not in cache:
            v, c = vision.get(label.asset_id), classify.get(label.asset_id)
            cache[label.asset_id] = predictions_from_outputs(
                v.output if v else None, c.output if c else None
            )
        idx = shot_idx.get(label.shot_id) if label.shot_id else None
        predicted = cache[label.asset_id].get((idx, label.field), MISSING)
        triples.append((label.field, label.expected, predicted))
    refs: dict[str, Any] = {}
    for stage, results in (("vision", vision), ("classify", classify)):
        models = {
            (r.output.get("model") or r.output.get("text_model") or {}).get("model_id")
            for r in results.values()
        } - {None}
        if models:
            refs[stage] = sorted(models)
    return triples, refs


async def run_evaluation(session: AsyncSession, label: str | None = None) -> EvalRun:
    """Compute and store an evaluation run (the caller commits)."""
    triples, refs = await collect_triples(session)
    run = EvalRun(
        label=label, model_refs=refs, golden_count=len(triples), metrics=compute_metrics(triples)
    )
    session.add(run)
    await session.flush()
    return run


def render_markdown(
    metrics: dict[str, Any], refs: dict[str, Any], created: datetime, n: int
) -> str:
    """A Markdown report: overall numbers, per-field table, per-class details, confusion."""
    overall = metrics["overall"]
    lines = [
        f"# Evaluation {created:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Golden labels: {n}",
        f"- Overall accuracy: {overall['accuracy']}",
        f"- Models: {', '.join(f'{k}: {", ".join(v)}' for k, v in refs.items()) or 'unknown'}",
        "",
        "| Field | n | Accuracy | Macro F1 |",
        "|---|---:|---:|---:|",
    ]
    fields = metrics["fields"]
    for name, data in fields.items():
        lines.append(f"| {name} | {data['n']} | {data['accuracy']} | {data['macro_f1']} |")
    for name, data in fields.items():
        lines += ["", f"## {name}", "", "| Class | Precision | Recall | F1 | Support |"]
        lines.append("|---|---:|---:|---:|---:|")
        for cls, c in data["classes"].items():
            lines.append(
                f"| {cls} | {c['precision']} | {c['recall']} | {c['f1']} | {c['support']} |"
            )
        lines += ["", "Confusion (expected -> predicted):", ""]
        for expected, predicted in data["confusion"].items():
            cells = ", ".join(f"{p} x{n_}" for p, n_ in predicted.items())
            lines.append(f"- {expected}: {cells}")
    return "\n".join(lines) + "\n"
