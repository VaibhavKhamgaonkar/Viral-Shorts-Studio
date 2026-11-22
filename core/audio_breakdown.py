import csv
import json
import sys
import tarfile
import wave
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import requests


RATE = 48000
GITHUB_MODELS = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
SEGMENTATION = "sherpa-onnx-pyannote-segmentation-3-0"
EMBEDDING = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
YAMNET = "https://huggingface.co/audiomagic/yamnet-onnx/resolve/f25b741/"
ELECTRONIC_TONES = {"Beep, bleep", "Busy signal", "Dial tone", "Telephone dialing, DTMF"}


def download_file(url, output, limit=60_000_000):
    with requests.get(url, stream=True, timeout=(10, 45)) as response:
        response.raise_for_status()
        with output.open("wb") as file:
            for block in response.iter_content(1024 * 256):
                if file.tell() + len(block) > limit:
                    raise ValueError("Audio model download exceeds its expected size.")
                file.write(block)


def prepare_models(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=folder) as directory:
        scratch = Path(directory)
        segmentation = folder / "speaker-segmentation.onnx"
        if not segmentation.is_file():
            download_file(GITHUB_MODELS + f"speaker-segmentation-models/{SEGMENTATION}.tar.bz2",
                          scratch / "model.tar.bz2")
            with tarfile.open(scratch / "model.tar.bz2", "r:bz2") as archive:
                member = next(item for item in archive.getmembers() if item.isfile() and item.name.endswith("/model.onnx"))
                if member.size > 60_000_000:
                    raise ValueError("Speaker model exceeds its expected size.")
                (scratch / "model.onnx").write_bytes(archive.extractfile(member).read())
            (scratch / "model.onnx").replace(segmentation)
        for name, url in (
            (EMBEDDING, GITHUB_MODELS + "speaker-recongition-models/" + EMBEDDING),
            ("yamnet.onnx", YAMNET + "yamnet.onnx"),
            ("yamnet_class_map.csv", YAMNET + "yamnet_class_map.csv"),
        ):
            if not (folder / name).is_file():
                download_file(url, scratch / name)
                (scratch / name).replace(folder / name)


def read_audio(path):
    with wave.open(str(path), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (RATE, 2, 2):
            raise ValueError("Expected stereo 48 kHz PCM audio.")
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768


def write_audio(path, samples):
    pcm = np.round(np.clip(samples, -1, 32767 / 32768) * 32768).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        stream.writeframes(pcm.tobytes())


def mono16(samples):
    from scipy.signal import resample_poly

    return resample_poly(samples.mean(axis=1), 1, 3).astype(np.float32)


def speaker_turns(audio, models, speakers):
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / "speaker-segmentation.onnx"),
            ), num_threads=2,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(models / EMBEDDING), num_threads=2),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers or -1, threshold=.5),
        min_duration_on=.28, min_duration_off=.2,
    )
    if not config.validate():
        raise RuntimeError("Could not initialize speaker detection.")
    detector = sherpa_onnx.OfflineSpeakerDiarization(config)
    return [(turn.start, turn.end, turn.speaker) for turn in detector.process(mono16(audio)).sort_by_start_time()]


def speaker_spans(turns, duration):
    starts = {}
    changes = defaultdict(list)
    for start, stop, speaker in sorted(turns):
        start, stop = max(0., start), min(duration, stop)
        if stop <= start:
            continue
        starts.setdefault(speaker, len(starts) + 1)
        changes[start].append((speaker, 1))
        changes[stop].append((speaker, -1))
    spans = defaultdict(list)
    active = defaultdict(int)
    previous = 0.
    for boundary in sorted(changes):
        if boundary > previous and active:
            if len(active) > 1:
                label = "Overlapping voices"
            else:
                number = starts[next(iter(active))]
                label = f"Speaker {number}" if number <= 8 else "Other voices"
            spans[label].append((previous, boundary))
        for speaker, delta in changes[boundary]:
            active[speaker] += delta
            if not active[speaker]:
                del active[speaker]
        previous = boundary
    return spans


def sound_spans(audio, models):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    model = ort.InferenceSession(str(models / "yamnet.onnx"), options, providers=["CPUExecutionProvider"])
    with (models / "yamnet_class_map.csv").open(encoding="utf-8", newline="") as file:
        labels = [row["display_name"] for row in csv.DictReader(file)]
    samples = mono16(audio)
    spans = defaultdict(list)
    # Use short chunks so the model's intermediate spectrograms stay small.
    for begin in range(0, len(samples), 16000 * 12):
        block = samples[begin:begin + 16000 * 12]
        scores, = model.run(["output_0"], {model.get_inputs()[0].name: block})
        for number, row in enumerate(scores):
            best = int(row.argmax())
            label = labels[best]
            if row[best] < .50 or label in {"Speech", "Silence", "Noise", "Sound effect", "Inside, small room"}:
                continue
            if label in ELECTRONIC_TONES:
                label = "Beeps and electronic tones"
            start = begin / 16000 + number * .48
            end = min((begin + len(block)) / 16000, start + .48)
            if end > start:
                spans[label].append((start, end))
    longest = sorted(spans, key=lambda name: -sum(end - start for start, end in spans[name]))
    return {label: spans[label] for label in longest[:5]}


def partition_audio(audio, groups, remainder):
    used = np.zeros(len(audio), dtype=bool)
    for label, spans in groups.items():
        track = np.zeros_like(audio)
        for start, end in sorted(spans):
            left, right = round(start * RATE), min(len(audio), round(end * RATE))
            track[left:right] = audio[left:right]
            used[left:right] = True
        if np.any(np.abs(track) >= 1 / 32768):
            yield label, track, spans
    yield remainder, np.where(used[:, None], 0, audio), []


def analyze(source, voice, folder, models, speakers):
    prepare_models(models)
    original = read_audio(source)
    isolated = read_audio(voice)[:len(original)]
    if len(isolated) < len(original):
        isolated = np.pad(isolated, ((0, len(original) - len(isolated)), (0, 0)))
    background = original - isolated
    duration = len(original) / RATE
    turns = speaker_spans(speaker_turns(original, models, speakers), duration)
    sounds = sound_spans(background, models)
    tracks = []
    # Use a shared gain so the per-track PCM files sum to the same balance.
    peak = max(1.0, float(np.max(np.abs(background))), float(np.max(np.abs(isolated))))
    for kind, samples, groups, remainder in (
        ("voice", isolated, turns, "Unassigned voice"),
        ("sound", background, sounds, "Other background"),
    ):
        for label, audio, spans in partition_audio(samples, groups, remainder):
            if not np.any(np.abs(audio) >= 1 / 32768):
                continue
            name = f"track-{len(tracks) + 1}.wav"
            write_audio(folder / name, audio / peak)
            if spans:
                first = spans[0][0]
            else:
                # Find an audible point in a residual track for quick audition.
                audible = np.flatnonzero(np.max(np.abs(audio), axis=1) >= .012)
                first = max(0, (int(audible[0]) / RATE - .05)) if len(audible) else 0
            tracks.append({"id": name, "label": label, "kind": kind, "first": round(first, 3)})
    (folder / "tracks.json").write_text(json.dumps({"duration": duration, "tracks": tracks}), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--setup":
        prepare_models(Path(sys.argv[2]))
    else:
        analyze(*map(Path, sys.argv[1:5]), int(sys.argv[5]))
