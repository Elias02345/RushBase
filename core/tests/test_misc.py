import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from rushbase_core import (
    DEFAULT_ENABLED_STAGES,
    MediaType,
    Observation,
    ServerLeaseHeldError,
    ServerLeaseLostError,
    Stage,
    acquire_server_lease,
    batch_for,
    batch_root_of,
    build_job_plan,
    evaluate,
    init_library,
    media_type_for,
    open_library,
    quick_fingerprint,
    release_server_lease,
    renew_server_lease,
    uuid7,
    uuid7_timestamp_ms,
    version_lt,
)
from rushbase_core.fingerprint import SAMPLE_SIZE

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_uuid7_is_valid_monotonic_and_time_ordered() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    sample = ids[0]
    assert isinstance(sample, UUID)
    assert sample.version == 7
    assert sample.variant == "specified in RFC 4122"
    assert abs(uuid7_timestamp_ms(sample) - int(datetime.now(UTC).timestamp() * 1000)) < 5000


def test_uuid7_embeds_explicit_timestamp() -> None:
    assert uuid7_timestamp_ms(uuid7(1_700_000_000_123)) in range(
        1_700_000_000_123, 1_700_000_001_000
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.MP4", MediaType.VIDEO),
        ("a.R3D", MediaType.VIDEO),
        ("a.cr3", MediaType.PHOTO),
        ("a.HEIC", MediaType.PHOTO),
        ("a.wav", MediaType.AUDIO),
        ("a.xmp", MediaType.OTHER),
        ("noext", MediaType.OTHER),
    ],
)
def test_media_type_by_extension(name: str, expected: MediaType) -> None:
    assert media_type_for(name) is expected


# --- stability -------------------------------------------------------------------------


def test_stability_needs_two_scans_and_the_time_window() -> None:
    mtime = T0 - timedelta(hours=1)
    first = evaluate(None, 100, mtime, T0)
    assert (first.stable_count, first.stable, first.changed) == (1, False, True)
    prev = Observation(100, mtime, first.stable_count, first.last_changed_at)
    second = evaluate(prev, 100, mtime, T0 + timedelta(seconds=60))
    assert (second.stable_count, second.stable) == (2, False)  # two scans, only 60 s quiet
    prev = Observation(100, mtime, 2, second.last_changed_at)
    third = evaluate(prev, 100, mtime, T0 + timedelta(seconds=121))
    assert third.stable


def test_growth_or_mtime_change_restarts_the_window() -> None:
    mtime = T0
    prev = Observation(100, mtime, 5, T0 - timedelta(hours=1))
    grown = evaluate(prev, 200, mtime, T0)
    assert (grown.stable_count, grown.stable, grown.changed) == (1, False, True)
    touched = evaluate(prev, 100, mtime + timedelta(seconds=1), T0)
    assert touched.changed
    assert touched.last_changed_at == T0


def test_custom_thresholds() -> None:
    prev = Observation(1, T0, 1, T0 - timedelta(seconds=5))
    assert evaluate(prev, 1, T0, T0, required_scans=2, window_seconds=5).stable
    assert not evaluate(prev, 1, T0, T0, required_scans=3, window_seconds=5).stable


# --- pipeline --------------------------------------------------------------------------


def deps(plan: list, stage: Stage) -> tuple[Stage, ...]:  # type: ignore[type-arg]
    return next(spec.depends_on for spec in plan if spec.stage is stage)


def test_full_video_plan_follows_the_specified_order() -> None:
    plan = build_job_plan(list(Stage))
    assert [s.stage for s in plan] == list(Stage)
    assert deps(plan, Stage.PROBE) == ()
    assert deps(plan, Stage.HASH) == ()  # probe and hash run in parallel
    assert deps(plan, Stage.SHOTS) == (Stage.PROBE, Stage.PREVIEW)
    assert deps(plan, Stage.CLASSIFY) == (Stage.AUDIO, Stage.EMBED, Stage.VISION)
    assert deps(plan, Stage.FINALIZE) == (Stage.HASH, Stage.CLASSIFY)


def test_disabled_stages_are_bridged() -> None:
    plan = build_job_plan(DEFAULT_ENABLED_STAGES)
    assert [s.stage for s in plan] == [
        Stage.PROBE,
        Stage.HASH,
        Stage.PREVIEW,
        Stage.SHOTS,
        Stage.FINALIZE,
    ]
    # audio/embed/vision/classify are off: finalize waits for what they would have waited for
    assert deps(plan, Stage.FINALIZE) == (Stage.PROBE, Stage.HASH, Stage.PREVIEW, Stage.SHOTS)
    only = build_job_plan(["probe"])
    assert [(s.stage, s.depends_on) for s in only] == [
        (Stage.PROBE, ()),
        (Stage.FINALIZE, (Stage.PROBE,)),
    ]


def test_bridging_when_a_middle_stage_is_off() -> None:
    plan = build_job_plan(["probe", "shots", "finalize"])  # preview disabled
    assert deps(plan, Stage.SHOTS) == (Stage.PROBE,)


def test_media_type_drops_inapplicable_stages() -> None:
    photo = build_job_plan(list(Stage), MediaType.PHOTO)
    assert Stage.SHOTS not in {s.stage for s in photo}
    assert Stage.AUDIO not in {s.stage for s in photo}
    assert deps(photo, Stage.EMBED) == (Stage.PROBE, Stage.PREVIEW)
    other = build_job_plan(list(Stage), MediaType.OTHER)
    assert {s.stage for s in other} == {Stage.PROBE, Stage.HASH, Stage.FINALIZE}


def test_every_dependency_refers_to_a_planned_job() -> None:
    for media in MediaType:
        plan = build_job_plan(list(Stage), media)
        present = {s.stage for s in plan}
        assert all(set(s.depends_on) <= present for s in plan)


# --- batches, versions, fingerprint ------------------------------------------------------


def test_batch_label_and_hint_date() -> None:
    hint = batch_for("Input/2026-10-12_Drehtag_Standort-A/x/y.mp4", date(2026, 10, 13))
    assert hint is not None
    assert hint.input_path == "Input/2026-10-12_Drehtag_Standort-A"
    assert hint.label == "Drehtag_Standort-A"
    assert hint.hint_date == date(2026, 10, 12)
    assert not hint.auto


def test_invalid_hint_dates_are_ignored() -> None:
    assert batch_for("Input/2026-13-45_x/a.mp4", date(2026, 1, 1)).hint_date is None  # type: ignore[union-attr]
    assert batch_for("Input/Holiday/a.mp4", date(2026, 1, 1)).hint_date is None  # type: ignore[union-attr]


def test_loose_files_go_to_a_per_day_auto_batch() -> None:
    hint = batch_for("Input/clip.mov", date(2026, 10, 13))
    assert hint is not None
    assert hint.auto
    assert hint.input_path == "Input/@auto/2026-10-13"
    assert batch_root_of(hint.input_path) == "Input"
    assert batch_for("Content/a.mp4", date(2026, 1, 1)) is None


def test_version_comparison_is_numeric() -> None:
    assert version_lt("2", "10")
    assert version_lt("1.9", "1.10")
    assert not version_lt("2.0", "2.0")
    assert version_lt("2024-01-01", "2024-06-01")


def test_quick_fingerprint_samples_and_detects_changes(tmp_path: Path) -> None:
    small = tmp_path / "s.bin"
    small.write_bytes(b"hello")
    assert quick_fingerprint(small).startswith("5:")
    big = tmp_path / "b.bin"
    data = bytearray(os.urandom(SAMPLE_SIZE * 8))
    big.write_bytes(bytes(data))
    first = quick_fingerprint(big)
    assert first.startswith(f"{len(data)}:")
    data[SAMPLE_SIZE * 2] ^= 0xFF  # a byte outside the samples is deliberately not seen
    big.write_bytes(bytes(data))
    assert quick_fingerprint(big) == first
    data[10] ^= 0xFF  # but a change inside the first sample is
    big.write_bytes(bytes(data))
    assert quick_fingerprint(big) != first


# --- server lease ----------------------------------------------------------------------


def test_lease_acquire_renew_release(tmp_path: Path) -> None:
    init_library(tmp_path)
    lease = acquire_server_lease(tmp_path, "a", ttl_seconds=120, now=T0)
    assert lease.expires_at == T0 + timedelta(seconds=120)
    assert open_library(tmp_path).server_lease == lease
    renewed = renew_server_lease(tmp_path, "a", now=T0 + timedelta(seconds=30))
    assert renewed.expires_at == T0 + timedelta(seconds=150)
    release_server_lease(tmp_path, "b")  # not the holder: no effect
    assert open_library(tmp_path).server_lease is not None
    release_server_lease(tmp_path, "a")
    assert open_library(tmp_path).server_lease is None


def test_second_server_is_refused_until_expiry_or_force(tmp_path: Path) -> None:
    init_library(tmp_path)
    acquire_server_lease(tmp_path, "a", now=T0)
    with pytest.raises(ServerLeaseHeldError) as excinfo:
        acquire_server_lease(tmp_path, "b", now=T0 + timedelta(seconds=60))
    assert excinfo.value.holder == "a"
    # expired lease can be taken over without force
    assert acquire_server_lease(tmp_path, "b", now=T0 + timedelta(seconds=121)).holder == "b"
    # explicit takeover while the lease is live
    forced = acquire_server_lease(tmp_path, "c", now=T0 + timedelta(seconds=130), force=True)
    assert forced.holder == "c"


def test_taken_over_holder_notices_on_renew(tmp_path: Path) -> None:
    init_library(tmp_path)
    acquire_server_lease(tmp_path, "a", now=T0)
    acquire_server_lease(tmp_path, "b", now=T0 + timedelta(seconds=10), force=True)
    with pytest.raises(ServerLeaseLostError):
        renew_server_lease(tmp_path, "a", now=T0 + timedelta(seconds=20))


def test_manifest_writes_leave_no_temp_files(tmp_path: Path) -> None:
    init_library(tmp_path)
    acquire_server_lease(tmp_path, "a", now=T0)
    leftovers = [p.name for p in (tmp_path / ".rushbase").iterdir() if p.name.endswith(".rbtmp")]
    assert leftovers == []


def test_batch_label_drops_the_date_prefix_but_keeps_the_hint() -> None:
    from datetime import date

    from rushbase_core.batches import batch_for, label_from_folder

    hint = batch_for("Input/2026-10-12_Drehtag_Standort-A/a.mp4", date(2026, 10, 16))
    assert hint is not None
    assert hint.label == "Drehtag_Standort-A"
    assert hint.hint_date == date(2026, 10, 12)
    assert label_from_folder("2026-10-12") == "2026-10-12"
    assert label_from_folder("Messe Berlin") == "Messe Berlin"


def test_pure_audio_plan_has_no_image_stages() -> None:
    plan = {job.stage: job.depends_on for job in build_job_plan(list(Stage), MediaType.AUDIO)}
    assert Stage.EMBED not in plan
    assert Stage.VISION not in plan
    assert Stage.SHOTS not in plan
    assert Stage.AUDIO in plan[Stage.CLASSIFY]
