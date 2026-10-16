"""Generate the SigLIP 2 golden fixture with the reference implementation (transformers).

Never run this on the host: it needs PyTorch and transformers. Use the wrapper next to it:

    worker/tests/fixtures/ai/make-siglip-fixture.sh

which runs this file in a throw-away container (``rushbase-m4-ref``) and writes
``siglip2_golden.json`` and ``siglip_test.png`` into this directory. The worker's ONNX
pipeline must reproduce the reference vectors (cosine >= 0.99 fp16, >= 0.97 int8; ADR-0007).
"""

import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

OUT = Path(sys.argv[1])
MODEL = "google/siglip2-base-patch16-256"
TEXTS = [
    "a photo of a cat",
    "ein Foto von einer Katze",
    "Weitwinkelaufnahme eines Strandes bei Sonnenuntergang",
    "a red square on a blue background",
]


def _tensor(output):  # newer transformers wrap the pooled vector in a model output
    return getattr(output, "pooler_output", output)


def test_image() -> Image.Image:
    """Deterministic, content-rich 320x240 test image (gradient, shapes, noise)."""
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:240, 0:320]
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[..., 0] = (x * 255 // 319).astype(np.uint8)
    image[..., 1] = (y * 255 // 239).astype(np.uint8)
    image[..., 2] = 80
    image[60:180, 100:220] = (220, 30, 30)
    image[90:150, 130:190] = (30, 60, 220)
    noise = rng.integers(-12, 12, size=image.shape)
    return Image.fromarray(np.clip(image.astype(int) + noise, 0, 255).astype(np.uint8))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    image = test_image()
    image.save(OUT / "siglip_test.png")
    processor = AutoProcessor.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL).eval()
    with torch.no_grad():
        pixel = processor(images=image, return_tensors="pt")
        image_embedding = _tensor(model.get_image_features(**pixel))
        text_inputs = processor(
            text=[t.lower() for t in TEXTS], padding="max_length", max_length=64,
            truncation=True, return_tensors="pt",
        )  # fmt: skip
        text_embedding = _tensor(model.get_text_features(**text_inputs))
    normalise = torch.nn.functional.normalize
    golden = {
        "model": MODEL,
        "transformers_input_ids": text_inputs["input_ids"].tolist(),
        "texts": TEXTS,
        "image_embedding": normalise(image_embedding, dim=-1)[0].tolist(),
        "text_embeddings": normalise(text_embedding, dim=-1).tolist(),
        "pixel_values_sample": pixel["pixel_values"][0, :, 0, :8].tolist(),
    }
    (OUT / "siglip2_golden.json").write_text(json.dumps(golden))
    print("wrote", OUT / "siglip2_golden.json")


if __name__ == "__main__":
    main()
