# The voice loop

```
browser mic ──► WebM/Opus ──► ffmpeg ──► 16kHz mono WAV ──► whisper.cpp
                                                                │
                                                          transcript
                                                                │
                                              LLM  ◄──►  flight service tools
                                                                │
                                                             reply text
                                                                │
                                          Piper ──► WAV ──► base64 ──► browser
```

Open http://localhost:8000 with `make ai` running. Hold the button (or the
spacebar) to talk, release to send.

## Measured latency

On an M5, `small.en` + `qwen2.5:7b-instruct` + Piper, for a short question:

| Stage | Typical |
|---|---|
| STT | 490–610 ms |
| LLM (incl. tool round trips) | 2,500–6,200 ms |
| Tools | 12–25 ms |
| TTS | 640–1,120 ms |
| **Total** | **~3.6–8 s** |

**The LLM dominates by an order of magnitude.** Neither speech stage is worth
optimising until that changes — and the cheapest fix is `LLM_PROVIDER=groq`,
which is why the provider interface exists.

## Two findings that shaped the code

### Speech recognition mangles identifiers

Flight numbers and booking references are exactly the thing speech recognition
handles worst, and exactly the thing an airline agent cannot get wrong.

| Spoken | Transcribed |
|---|---|
| `AI858` | `AI 858` — a space, on both model sizes |
| `AI858` | `AIA-858` — `base.en` hallucinating a letter |
| `booking 7MGFXC` | `Pooking7MGFXC` — word mangled, reference intact |

Two responses, both necessary:

1. **Identifiers are normalised at the tool boundary** — spaces, hyphens, dots
   and commas stripped, then upper-cased ([`_normalise_identifier`](../services/ai/app/tools/flight.py)).
   This lives in the AI service, where noisy speech meets structured data, not in
   the flight service, which is a backend API and should stay strict.
2. **`small.en` is the default, not `base.en`.** `base.en` hallucinates letters
   into flight numbers. The accuracy costs ~240 ms (216 → 458 ms on a 3-second
   clip), which is noise next to a 4-second LLM call.

### Silence must not reach the model

An empty transcript is answered directly with "Sorry, I didn't catch that."
Passing an empty string to the model produces a confident reply to nothing at all.

## Design notes

- **Audio returns as base64 inside the JSON**, so one round trip carries the
  reply, what we heard, and what the agent did. Phase 12 moves it to S3 and
  returns a URL; at conversational lengths inlining it is not worth a second
  request.
- **The transcript is returned separately from the reply.** A wrong answer is
  usually a misheard question, and without the transcript you cannot tell the
  two apart.
- **TTS failure degrades to a silent reply** rather than losing the turn. The
  text is already correct and the caller can still display it.
- **ffmpeg is required.** whisper.cpp accepts only 16 kHz mono WAV; browsers
  record WebM/Opus. Skipping that conversion is the most common reason local STT
  "silently returns nothing".

## Checking it still works

```bash
make test-voice   # speaks questions at the agent, asserts the right tools fire
```

This catches what text tests structurally cannot: recognition mangling an
identifier so that a lookup which should succeed does not.
