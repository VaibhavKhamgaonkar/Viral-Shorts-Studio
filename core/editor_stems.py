import sys
from pathlib import Path


MODEL = "UVR-MDX-NET-Inst_HQ_3.onnx"


def separate_audio(source, workdir, model_dir):
    from audio_separator.separator import Separator

    separator = Separator(output_dir=str(workdir), model_file_dir=str(model_dir), output_format="WAV",
                          use_soundfile=True, normalization_threshold=1.0)
    separator.load_model(MODEL)
    separator.separate(str(source), {"Vocals": "voice", "Instrumental": "background"})


if __name__ == "__main__":
    separate_audio(*map(Path, sys.argv[1:]))
