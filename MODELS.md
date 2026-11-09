# Pinned models and attribution

Application license: MIT. Model weights and dependencies retain their own licenses; the application
license does not relicense them. Models are not checked into Git. Docker downloads them from fixed
upstream URLs and verifies exact SHA-256s. Consult upstream license terms before redistributing.

| Asset | Upstream / attribution | License | SHA-256 |
| --- | --- | --- | --- |
| `speaker.onnx` | [WeSpeaker](https://github.com/wenet-e2e/wespeaker), VoxCeleb ResNet34-LM, ONNX distributed by [k2-fsa sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) | WeSpeaker code Apache-2.0; VoxCeleb pretrained weights CC BY 4.0 per [upstream model license](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md#model-license). [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This distribution uses the upstream ONNX conversion, unchanged. | `e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012` |
| `vad.onnx` | [Silero Team](https://github.com/snakers4/silero-vad), ONNX distributed by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx) | MIT; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` |

Inference uses sherpa-onnx (Apache-2.0), CPU provider, two embedding threads and one VAD thread.
Exact dependency versions and artifact hashes are committed in `uv.lock`. Embeddings are normalized
256-dimensional float32, with model ID `wespeaker-en-resnet34-lm:e9848563da86`. Comparisons require the
same model ID. No automatic model updates or fallback models exist. A changed upstream download
fails checksum verification, including during Docker build.

These models were not trained by this project. Speaker similarity and VAD are not liveness or
synthetic-speech detectors. No demographic, sentiment or protected-attribute analysis is included.
Upstream VoxCeleb benchmark results are not accuracy claims for telephone audio or this module.
