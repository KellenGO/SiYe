"""Standalone optional ASR worker. Only receives a temporary media path, never credentials."""

import json
import logging
import sys
from pathlib import Path


def emit(event):
    print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)


def decode_bounded(path, max_seconds):
    import av
    import numpy as np
    chunks, samples = [], 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(path) as container:
        stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if stream is None:
            raise ValueError("audio")
        if stream.duration and float(stream.duration * stream.time_base) > max_seconds:
            raise ValueError("duration")
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                values = converted.to_ndarray().reshape(-1)
                samples += values.size
                if samples > 16000 * max_seconds:
                    raise ValueError("duration")
                chunks.append(values)
        for converted in resampler.resample(None):
            values = converted.to_ndarray().reshape(-1)
            samples += values.size
            if samples > 16000 * max_seconds:
                raise ValueError("duration")
            chunks.append(values)
    if not chunks:
        raise ValueError("audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def run(payload):
    # Dynamic import keeps the optional engine and its DLLs out of the main EXE.
    engine = __import__("faster_whisper", fromlist=["WhisperModel"])
    emit({"type": "progress", "stage": "decode"})
    try:
        audio = decode_bounded(payload["media"], payload["max_seconds"])
    except ValueError as error:
        raise ValueError("duration" if str(error) == "duration" else "no_audio" if str(error) == "audio" else "decode") from None
    except Exception:
        raise ValueError("decode") from None
    emit({"type": "progress", "stage": "model"})
    try:
        model = engine.WhisperModel(payload["model"], device="cpu", compute_type="int8",
            cpu_threads=4, num_workers=1, download_root=payload["models"], local_files_only=True)
    except Exception:
        emit({"type": "progress", "stage": "download"})
        try:
            model = engine.WhisperModel(payload["model"], device="cpu", compute_type="int8",
                cpu_threads=4, num_workers=1, download_root=payload["models"])
        except Exception:
            raise ValueError("model") from None
    emit({"type": "progress", "stage": "transcribe"})
    try:
        segments, info = model.transcribe(audio, language=payload.get("language"), vad_filter=True,
            initial_prompt="以下可能包含中文语音。", beam_size=5)
        entries, size = [], 0
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            size += len(text)
            if len(entries) >= 10000 or size > 256000:
                raise ValueError("transcript_limit")
            entries.append({"start": segment.start, "end": segment.end, "text": text})
    except Exception:
        raise ValueError("transcript") from None
    return {"type": "done", "entries": entries, "language": info.language, "duration": len(audio) / 16000}


def component_status(payload):
    import faster_whisper
    import av
    import numpy
    from faster_whisper.utils import download_model
    ready = False
    try:
        directory = Path(download_model(payload["model"], cache_dir=payload["models"], local_files_only=True))
        ready = all((directory / name).is_file() and (directory / name).stat().st_size > 0
                    for name in ("model.bin", "config.json", "tokenizer.json"))
    except Exception:
        pass
    return {"type": "done", "engine_version": faster_whisper.__version__, "model_ready": ready}


def main():
    logging.disable(logging.CRITICAL)
    try:
        payload = json.loads(sys.stdin.buffer.readline(16384))
        emit(component_status(payload) if payload.get("mode") == "status" else run(payload))
    except Exception as error:
        code = "unavailable" if isinstance(error, ImportError) else str(error) if isinstance(error, ValueError) and str(error) in {"duration", "model", "decode", "no_audio", "transcript"} else "failed"
        emit({"type": "error", "code": code})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
