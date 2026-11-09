"""CPU-only inference. No transcription, remote fetch, raw audio files or demographic inference."""

import hashlib
import io
import math
import threading
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

MODEL_ID = "wespeaker-en-resnet34-lm:e9848563da86"
ASSETS = {
    "speaker.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx",
        "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
    ),
    "vad.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    ),
}
MAX_AUDIO_SECONDS = 60
MAX_AUDIO_BYTES = 4_000_000


class AudioProblem(Exception):
    pass


def decode_wav(raw, channel):
    if len(raw) > MAX_AUDIO_BYTES:
        raise AudioProblem("audio_too_large")
    try:
        with wave.open(io.BytesIO(raw), "rb") as w:
            rate, channels, frames = w.getframerate(), w.getnchannels(), w.getnframes()
            if rate not in {8000, 16000, 48000} or channels not in {1, 2} or w.getsampwidth() != 2:
                raise AudioProblem("requires_pcm16_wav_8_16_48khz")
            if w.getcomptype() != "NONE" or frames / rate > MAX_AUDIO_SECONDS:
                raise AudioProblem("audio_duration_or_encoding")
            if channels == 2 and channel is None:
                raise AudioProblem("caller_channel_required")
            selected = channel if channel is not None else 0
            if selected >= channels:
                raise AudioProblem("invalid_channel")
            pcm = w.readframes(frames)
            if len(pcm) != frames * channels * 2 or frames == 0:
                raise AudioProblem("truncated_or_empty_audio")
            samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, channels)[:, selected]
            return samples.astype(np.float32) / 32768, rate
    except (wave.Error, EOFError, ValueError):
        raise AudioProblem("invalid_wav") from None


class Engine:
    model_id = MODEL_ID

    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.extractor = None

    def _load(self):
        if self.extractor is not None:
            return
        for path, name in [
            (self.settings.speaker_path, "speaker.onnx"),
            (self.settings.vad_path, "vad.onnx"),
        ]:
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != ASSETS[name][1]:
                raise RuntimeError("model_missing_or_checksum_mismatch")
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=self.settings.speaker_path, num_threads=2, provider="cpu", debug=False
        )
        if not config.validate():
            raise RuntimeError("invalid_model_config")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def compute(self, samples, rate, operation):
        # Serialize native calls to cap memory/CPU and avoid shared extractor races.
        with self.lock:
            self._load()
            import sherpa_onnx

            if rate != 16000:
                factor = math.gcd(rate, 16000)
                samples = resample_poly(samples, 16000 // factor, rate // factor).astype(np.float32)
            clip_fraction = float(np.mean(np.abs(samples) >= 0.999))
            if clip_fraction > 0.05:
                raise AudioProblem("excessive_clipping")
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = self.settings.vad_path
            config.silero_vad.min_silence_duration = 0.25
            config.silero_vad.min_speech_duration = 0.25
            config.sample_rate = 16000
            config.num_threads = 1
            vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=70)
            window = config.silero_vad.window_size
            speech = []
            for start in range(0, len(samples), window):
                chunk = samples[start : start + window]
                if len(chunk) != window:
                    chunk = np.pad(chunk, (0, window - len(chunk)))
                vad.accept_waveform(np.ascontiguousarray(chunk))
                while not vad.empty():
                    speech.append(np.array(vad.front.samples, dtype=np.float32))
                    vad.pop()
            vad.flush()
            while not vad.empty():
                speech.append(np.array(vad.front.samples, dtype=np.float32))
                vad.pop()
            voice = np.concatenate(speech) if speech else np.array([], dtype=np.float32)
            seconds = len(voice) / 16000
            required = 20 if operation == "enroll" else 6
            if seconds < required:
                raise AudioProblem("insufficient_speech")
            stream = self.extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=np.ascontiguousarray(voice))
            stream.input_finished()
            if not self.extractor.is_ready(stream):
                raise AudioProblem("insufficient_speech")
            v = np.asarray(self.extractor.compute(stream), dtype=np.float32)
            norm = np.linalg.norm(v)
            if not np.isfinite(v).all() or not np.isfinite(norm) or norm < 1e-8:
                raise RuntimeError("invalid_embedding")
            return v / norm, {
                "speech_seconds": round(seconds, 2),
                "clipping_fraction": round(clip_fraction, 5),
                "single_speaker": "caller_attested_not_diarized",
            }
