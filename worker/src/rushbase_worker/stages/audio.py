"""``audio`` stage: transcript (Whisper), audio events (CED), loudness, BPM and audio type."""

import asyncio
import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from rushbase_core import Stage
from rushbase_core.results import Estimated, Source
from rushbase_core.results_ai import AudioEvent, AudioOutput, AudioType, Transcript
from rushbase_worker.audio.events import EventClassifier, group_events, load_labels, total_seconds
from rushbase_worker.audio.events import window_rms_db as rms_per_window
from rushbase_worker.audio.extract import SAMPLE_RATE, extract_pcm, measure_loudness, write_wav
from rushbase_worker.audio.music import classify_audio_type, estimate_bpm
from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Job
from rushbase_worker.runtimes.manager import VAD_MODEL, Runtimes
from rushbase_worker.runtimes.whisper import transcribe
from rushbase_worker.stages.ai_base import AiStage
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.tools.process import ToolError

MIN_AUDIO_SECONDS = 0.1


def thresholds_for(classifier: EventClassifier, settings: WorkerSettings) -> dict[str, float]:
    """Per-tag thresholds: defaults from ``tags.toml`` overridden by the settings."""
    base = {name: spec.threshold for name, spec in classifier.tags.items()}
    return {**base, **settings.event_thresholds}


class AudioStage(AiStage):
    """Transcribes speech and classifies sound events of an asset's first audio stream."""

    stage = Stage.AUDIO
    requirements = Requirements(tools=("ffmpeg",), ai=True)

    async def process(self, job: Job, ctx: StageContext, runtimes: Runtimes) -> AudioOutput:
        if job.media_type == "photo":
            return AudioOutput(has_audio=False)
        source = ctx.source(job.primary().rel_path)
        try:
            samples = await extract_pcm(ctx.toolbox, source)
        except ToolError as exc:
            raise StageError(f"audio extraction failed: {exc}") from exc
        if samples.size < MIN_AUDIO_SECONDS * SAMPLE_RATE:
            return AudioOutput(has_audio=False)
        duration_s = samples.size / SAMPLE_RATE
        settings = ctx.settings

        events_model = runtimes.events_model
        await runtimes.ensure_models(events_model)
        classifier = self._classifier(runtimes, events_model)
        events = await asyncio.to_thread(self._events, classifier, samples, settings, duration_s)
        loudness = await measure_loudness(ctx.toolbox, source)

        transcript: Transcript | None = None
        if total_seconds(events, "speech") >= settings.speech_min_seconds:
            transcript = await self._transcribe(runtimes, ctx, samples)

        output = AudioOutput(
            has_audio=True,
            transcript=transcript,
            events=events,
            events_model=runtimes.model_ref(events_model),
            loudness=loudness,
        )
        if job.media_type == "audio":
            output = self._pure_audio(output, events, samples, duration_s)
        return output

    @staticmethod
    def _classifier(runtimes: Runtimes, model_id: str) -> EventClassifier:
        model = runtimes.models.file(model_id, "model")
        labels = load_labels(runtimes.models.file(model_id, "config"))
        return EventClassifier(runtimes.onnx_session(f"events:{model_id}", model), labels)

    @staticmethod
    def _events(
        classifier: EventClassifier,
        samples: NDArray[np.float32],
        settings: WorkerSettings,
        duration_s: float,
    ) -> list[AudioEvent]:
        window_s = min(settings.event_window_seconds, duration_s)
        probs, starts = classifier.probabilities(samples, window_s, settings.event_hop_seconds)
        window = round(window_s * SAMPLE_RATE)
        rms = rms_per_window(samples, [round(s * SAMPLE_RATE) for s in starts], window)
        return group_events(
            probs,
            classifier.labels,
            classifier.indices,
            starts_s=starts,
            window_s=window_s,
            hop_s=settings.event_hop_seconds,
            duration_s=duration_s,
            thresholds=thresholds_for(classifier, settings),
            rms_db=rms,
            silence_dbfs=settings.silence_dbfs,
            min_hits=settings.event_min_hits,
            merge_gap_s=settings.event_merge_gap_seconds,
        )

    @staticmethod
    async def _transcribe(
        runtimes: Runtimes, ctx: StageContext, samples: NDArray[np.float32]
    ) -> Transcript:
        asr = runtimes.asr_model
        await runtimes.ensure_models(asr)
        backend = await runtimes.ensure_backend("whisper")
        vad: Path | None = None
        if ctx.settings.whisper_vad:
            if ctx.settings.auto_download:
                await runtimes.ensure_models(VAD_MODEL)
            if runtimes.models.status(runtimes.spec(VAD_MODEL)).state == "installed":
                vad = runtimes.models.file(VAD_MODEL, "model")
        use_gpu = runtimes.gpu_backend and "cpu" not in backend.name
        with tempfile.TemporaryDirectory(prefix="rushbase-audio-") as tmp:
            wav = Path(tmp) / "audio.wav"
            write_wav(wav, samples)
            try:
                async with runtimes.whisper_slot():
                    result = await transcribe(
                        backend,
                        runtimes.models.file(asr, "model"),
                        wav,
                        use_gpu=use_gpu,
                        threads=ctx.settings.whisper_threads,
                        timeout=ctx.settings.long_tool_timeout_seconds,
                        vad_model=vad,
                        extra_lib_dirs=runtimes.backend_library_dirs(),
                    )
            except ToolError as exc:
                raise StageError(f"whisper failed: {exc}") from exc
        return Transcript(
            language=result.language,
            language_probability=result.language_probability,
            segments=result.segments,
            model=runtimes.model_ref(asr),
        )

    @staticmethod
    def _pure_audio(
        output: AudioOutput,
        events: list[AudioEvent],
        samples: NDArray[np.float32],
        duration_s: float,
    ) -> AudioOutput:
        classified = classify_audio_type(events, duration_s)
        audio_type: Estimated[AudioType] | None = (
            Estimated(value=classified[0], source=Source.AI, confidence=min(1.0, classified[1]))
            if classified
            else None
        )
        musical = classified is not None and classified[0].value in {"music", "mixed"}
        tempo = estimate_bpm(samples) if musical else None
        return output.model_copy(
            update={"audio_type": audio_type, "bpm": tempo[0] if tempo else None}
        )
