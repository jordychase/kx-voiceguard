# Accuracy and cost evaluation

No real-call accuracy has been established. `BENCHMARK.json` records only local functional inference
on OS-generated synthetic speech; it cannot establish false-match, false-nonmatch or spoof performance.
The test-suite's fake 3-D vectors test security/control flow only and never ship as a production model.

Before supplying a threshold, build a properly consented held-out corpus with separate caller
channels, different calls for genuine pairs, different speakers for impostor pairs, and no speaker
leakage between tuning and evaluation. Include actual codecs (8k PSTN, transcoding), channel/noise,
short segments, regional accents and repeat-call time gaps. Evaluate 1:1 and 1:N separately; the
probability of a false candidate grows with gallery size. Record model ID, domain, gallery size,
threshold, false accept/reject counts and confidence intervals. Use enough independent speakers
and trials for the desired false-accept rate; zero errors in a tiny sample proves very little.
Review disparate error rates with an appropriately consented, legally reviewed evaluation process.

Synthetic voices, replay and voice conversion need a separate held-out attack evaluation. This
release does not provide anti-spoof inference or pass a liveness check. Add an independently tested
open model (for example AASIST-family) as a separate signal only after validating current generators,
telephone compression and replay hardware. Never conflate speaker similarity with a live human.

## Cost

- MIT application: no software license or per-minute vendor charge from this project.
- WeSpeaker/Silero inference: runs on your CPU; no transcription API or GPU is required.
- Hosting, persistent volume, network, monitoring and operations are additional. Nothing here
  guarantees $0.06/audio-hour. A fixed staging server has idle cost even when processing is off.
- Measure cold and warm VAD+embedding runtime on the actual hosting CPU. Compute cost per audio-hour
  is the billed compute-hour rate divided by measured audio-hours processed per compute-hour, then
  add storage/IO/network and idle time. Search adds exact-scan CPU and database work.
- Local smoke: 31.77 seconds of synthetic speech processed in 0.49–0.74 seconds on the development
  Mac (43–65x real time), two embedding threads. This is not a cloud throughput or cost guarantee.
  Concurrent live traffic, real calls and container CPU throttling have not been benchmarked.

Pretrained inference and targeted calibration are the economical first step. Training a new
foundation model or adding Whisper transcription does not solve the speaker-matching requirement.
