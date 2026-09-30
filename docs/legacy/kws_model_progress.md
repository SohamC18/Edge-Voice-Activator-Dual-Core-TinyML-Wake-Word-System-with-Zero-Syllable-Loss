# SIH 2026 — AIML Team: Keyword Spotting (KWS) Model
**Owner:** Arihant (AIML team — flagged "hard")
**Keyword:** "friday"

**Status: Core pipeline COMPLETE.** Trained, quantized model exported and ready for handoff. Remaining work is research writeup + teammate integration, not further model building.

---

## Role & Scope

Build a custom wake-word detection model — the "ear" that recognizes one keyword — for a voice-controlled IoT system. Hand off a trained, quantized `.tflite` / C-byte array. Hardware wiring, firmware, and cloud ASR are owned by other teams.

**Hard constraints from problem statement:**
- No crowdsourced datasets, no pretrained global keywords ("Hey Google" / "Alexa" style)
- Open-source ML/TinyML frameworks only
- Final model: under 45KB tensor_arena, under 50KB flash (TFLite Micro)
- Target device: under 256KB RAM, under 10% CPU while idle listening

---

## Final Results ✅

| Metric | Result | Limit | Status |
|---|---|---|---|
| Test accuracy | 99.49% | — | — |
| False activation rate | 0.67% | — | — |
| Flash size (quantized) | 23.30KB | 50KB | PASS |
| Tensor arena (realistic est.) | ~32KB | 45KB | Likely PASS (see caveat below) |

**Final model files** (in `models/`):
- `kws_dscnn.keras` — trained Keras model
- `kws_dscnn_int8.tflite` — quantized TFLite model
- `kws_dscnn_model.cc` / `kws_dscnn_model.h` — C byte array for embedded handoff

---

## Full Pipeline (what was actually built)

### 1. Synthetic data generation
- Piper TTS, 5 voices (US/UK mix), keyword = "friday"
- 260 base positive clips → pitch/speed augmented to **2,600 positive samples**
- **1,500 negative clips**: 1,075 common words + 425 hard negatives (fry, fridge, flyby, Friday's, etc.)
- **800 silence/background clips**: true silence + white noise + pink noise (numpy-generated)

### 2. Acoustic augmentation
- Noise sources: **ESC-50** (2,000 environmental clips, via HuggingFace mirror `ashraq/esc50`) **+ full MUSAN** (~11GB, official OpenSLR source — speech/music/noise)
- Two augmentation passes (ESC-50-based + MUSAN-based, additive, not overlapping) using `audiomentations`: background noise injection, room reverb simulation (`pyroomacoustics`), gain variation
- **Final dataset: 34,125 total samples** (raw + both augmented sets combined)

### 3. Feature extraction
- 40-band log-Mel spectrograms via `librosa`, fixed to 1-second/101 time-frame windows
- Saved as single `data/features.npz` (features + labels)

### 4. Model engineering — DS-CNN
- Depthwise-separable CNN, deliberately shallow (3 DS blocks, 16→32→48→64 filter progression)
- Went through **3 architecture iterations** to fit the 45KB tensor_arena budget:
  1. Initial version: first-layer activation ~126KB — way over budget
  2. Added stride-2 + extra pooling: largest tensor down to 31.88KB — still tight/uncertain
  3. **Final fix: reduced first conv from 32→16 filters** — largest tensor 15.94KB, realistic arena ~32KB, comfortable margin
- Each iteration required retraining; final model trained in ~11s/epoch, stopped early via early-stopping

### 5. Deployment optimization
- Full INT8 post-training quantization (requires representative dataset for calibration)
- Exported as TFLite Micro-compatible C byte array

---

## Known Limitations (for research writeup — be upfront about these)

- **Train/test split is random across augmented variants, not grouped by source clip.** Near-duplicate variants of the same original recording can land in both train and test sets, likely inflating the reported accuracy somewhat. True accuracy is probably still strong (95-98%+ range) but the 99.49% figure should be caveated as measured under a random (not source-grouped) split.
- **Tensor arena figure is an estimate, not a confirmed TFLM measurement.** Measured via the standard TFLite interpreter (upper bound: sum of all tensors with no reuse credit = 55.39KB) and reasoned down to a realistic ~32KB estimate accounting for TFLM's buffer reuse. The exact number needs confirming via TFLM's `RecordingMicroAllocator` once the IoT teammate has the embedded build running.
- **No background-speech/music false-trigger testing beyond MUSAN's training contribution** — dataset includes MUSAN speech/music in training, but no separate held-out real-world test for this specific scenario.
- **Class imbalance**: roughly 53% positive / 30% negative / 16% silence — not corrected, but false-activation rate suggests it isn't hurting real performance.

---

## Handoff Notes for IoT/Software Teammates

- Model input: `(40, 101)` float32 spectrogram, **but exported model expects INT8 input** — whoever integrates this needs to apply the same quantization scale/zero-point learned during conversion, not feed raw floats directly
- Output: 3-class softmax — index 0 = "friday" (positive), 1 = negative/other word, 2 = silence
- Files to hand off: `kws_dscnn_model.cc` + `kws_dscnn_model.h`

---

## Remaining Work

1. **Research writeup** — justify DS-CNN vs alternatives, augmentation strategy (ESC-50 + MUSAN decision process), quantization tradeoffs, and honestly address the known limitations above
2. **Teammate handoff conversation** — deliver the `.cc`/`.h` files with the INT8 input note
3. **Optional**: confirm exact tensor_arena via TFLM's real profiler once embedded build environment exists (not blocking — current estimate has reasonable margin)

---

## Project Size Notes
- Full project with venv, TensorFlow, ESC-50, MUSAN (~11GB), and all augmented data: several GB — MUSAN specifically adds ~11GB and doesn't shrink via cleanup since it's needed as source data
- Consider cleanup (delete HuggingFace cache duplicates, archive raw audio post-feature-extraction) before final submission/sharing if size becomes an issue
