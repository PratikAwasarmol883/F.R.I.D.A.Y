# Milestone 5 Spec — Voice MVP

**Status:** Implemented (see section 11 for additions, deviations, and what was not verified).
**Source:** `architecture.md` §4.3 (voice interaction), §12 (voice and device architecture), §13 (Milestone 5); `plan.md` Phase 2.
**Builds on:** M1–M4 (auth, ask flow, tool policy, budget, audit).

## 1. Goal

A user can speak to FRIDAY from a Mac, hear a spoken answer, and always see when audio is being captured. The cloud API accepts a short audio utterance, transcribes it, runs the normal assistant flow, and returns the answer as text plus synthesized speech. Failure paths degrade gracefully (no microphone, no network, speech provider down, speech synthesis failure) and never crash the client.

**Done when (from the docs):**
- A spoken question reaches the cloud and a spoken response returns (verified end to end with fake speech providers; live providers are opt-in).
- Mute works: while muted nothing is captured or sent, and the state is visible.
- Missing audio devices, API errors, and speech-provider failures are handled without crashing.
- Voice capture and transmission are clearly indicated; audio is never persisted.

## 2. Decisions (defaults applied)

| # | Decision | Choice |
|---|---|---|
| D1 | Speech providers | Provider-neutral `TranscriptionProvider` / `SpeechProvider` adapters with an **OpenAI-compatible HTTP implementation** (`POST {base}/audio/transcriptions` multipart, `POST {base}/audio/speech` JSON). Works with OpenAI and compatible servers (Groq, local Whisper servers) via `SPEECH_BASE_URL`. Anthropic has no speech API. |
| D2 | Audio retention | **Audio is never stored** (neither uploads nor synthesized replies); only the transcript is stored, as the user's message, exactly like a typed question. |
| D3 | Response shape | One JSON response: transcript, answer, ids, usage, sources, and `audio {format, base64}`; speech failure degrades to text with `speech_error`. |
| D4 | Wake word | **Push-to-talk is the working default.** A `WakeWordDetector` interface is provided; a "Hey Friday" detector needs a custom-trained model that is not included. This is stated plainly rather than faked. |
| D5 | Mac client | A separate Python package `clients/macos/friday_client` with a hardware-independent, fully tested core (state machine, mute, failure handling, API client) and thin macOS adapters (microphone via `sounddevice`, playback via `afplay`, local fallback speech via `say`, token in Keychain). The adapters cannot be exercised here and are marked unverified. |
| D6 | Limits | 6 MB upload, formats wav/mp3/mp4/m4a/webm/ogg/flac sniffed by magic bytes, 100 voice requests per user per day, reply speech capped at 1,500 characters, 30 s speech-provider timeout, voice replies instructed to be short plain speech. |
| D7 | Phone/web clients | Out of scope (M5 is the Mac client); the API is client-neutral. |

## 3. Scope

**In:** speech adapters (HTTP + fakes); `POST /assistant/voice`; voice style for the assistant prompt; audio validation; speakable-text preparation; per-user daily voice limit; audit events; Mac client package with tests; settings, docs, compose passthrough.

**Out:** streaming/real-time audio, barge-in, speaker identification, wake-word model training, phone and web clients, storing or replaying audio, per-utterance language detection beyond provider defaults, custom voices.

## 4. Speech adapters (`adapters/speech/`)

- `TranscriptionProvider.transcribe(audio, audio_format, timeout) -> Transcript(text)`; `SpeechProvider.synthesize(text, timeout) -> SynthesizedAudio(data, audio_format)`.
- HTTP implementation sends `Authorization: Bearer <key>`; transcription is multipart (`file`, `model`, optional `language`, `response_format=json`) and reads `text`; synthesis posts `model`, `input`, `voice`, `response_format=mp3` and reads the binary body, bounded to 10 MB.
- Errors map like the LLM adapter: timeout → `ProviderTimeoutError`; 429, 5xx, and connection errors → `ProviderUnavailableError`; other failures → `ProviderError`. Provider bodies and messages are never surfaced or logged. Redirects are not followed.
- `SPEECH_BASE_URL` must be `https`, or `http` only for `localhost`/`127.0.0.1`; validated at startup. The key is a `SecretStr` and never logged.
- Fakes for tests; an opt-in live test is skipped without a key.

## 5. API

`POST /api/v1/assistant/voice` — `multipart/form-data`, authenticated:

| Field | Notes |
|---|---|
| `audio` | required file, ≤ 6 MB; format verified from magic bytes, the declared content type is not trusted |
| `conversation_id` | optional UUID (owner-checked like `ask`) |
| `speak` | optional boolean, default true; false skips synthesis |

Flow: auth → size/format validation → daily voice limit → transcribe → empty transcript is `NO_SPEECH_DETECTED` → normal ask flow with the voice style (budget, tools, history, audit all apply) → optional synthesis of speakable text → response. Response: `{transcript, answer, conversation_id, message_id, usage, sources, tools_used, audio: {format, base64} | null, speech_error: string | null}`.

New error codes (same envelope): `PAYLOAD_TOO_LARGE` (413), `UNSUPPORTED_MEDIA_TYPE` (415), `NO_SPEECH_DETECTED` (422), `VOICE_LIMIT` (429). Provider failures use the existing `PROVIDER_*` codes. Request-size limits become per-path (64 KB default, 6 MB for this endpoint); uploads without a length are bounded while reading.

## 6. Behavior details

- **Voice style:** the system prompt gains a suffix asking for one to three short plain-speech sentences with no markdown, lists, or URLs. Text-only behavior is unchanged.
- **Speakable text:** markdown syntax and code fences removed, URLs replaced by "a link", whitespace collapsed, truncated at a sentence boundary to 1,500 characters. The full answer is always returned as text.
- **Degradation:** transcription failure is a request error (nothing is stored); synthesis failure still returns the stored answer with `audio: null` and `speech_error` set to a stable code; the client then speaks the text locally.
- **Audit and privacy:** `assistant.voice` audit rows record outcome only; no audio, transcript, or answer in logs, audit rows, or events. Transcripts are bounded by the same 8,000-character message limit; over-long transcripts are rejected.
- **Limits and cost:** the daily voice limit is counted from audit rows; the existing daily token budget applies to the ask step; speech-provider spend is bounded by the upload cap, the reply cap, and the voice limit.

## 7. Mac client (`clients/macos/friday_client`)

- **State machine:** `idle → listening → thinking → speaking → idle`, plus `muted` and `error`. The indicator callback fires on **every** transition and **before** the microphone opens; a test enforces this ordering.
- **Mute:** while muted the wake/push-to-talk source is not polled, the microphone is never opened, nothing is sent, and playback stops immediately.
- **Failure handling:** no input device, recording failure, auth failure (401), voice limit, provider outage, network error, timeout, and unexpected API responses each produce a clear visible state and (where useful) a short local spoken message, then return to idle; the loop never exits on an error. Error text shown to the user never includes server internals.
- **API client:** base URL must be `https` unless localhost; token sent only in the `Authorization` header and never logged or shown in `repr`; multipart upload; bounded timeouts; stable mapping of the API error envelope.
- **Audio:** 16 kHz mono 16-bit WAV, recorded until silence or a maximum duration (RMS-based cutoff implemented in pure Python); temporary files for playback are deleted after use.
- **Tokens:** macOS Keychain via the `security` command, with an environment-variable fallback; a `login` command stores the token read from standard input.
- **Conversation continuity:** the conversation ID is reused for a configurable idle window, then a new conversation starts.
- Entry point: `python -m friday_client` (`--server`, `--push-to-talk`, `login`).

## 8. Tests

| Area | Cases |
|---|---|
| Adapters | request shape (URL, auth, multipart fields, JSON body), response parsing, error and status mapping, size bounds, no redirects, secrets never in errors, base-URL validation |
| Audio validation | magic-byte sniffing for each format, spoofed content types, empty/oversized/garbage uploads |
| Speakable text | markdown, code, URLs, truncation at sentence boundary |
| Voice API | happy path with fakes (transcript, stored message, audio base64), `speak=false`, conversation continuity and ownership, no speech, STT timeout/unavailable/error mapping with nothing stored, TTS failure degrades to text, daily limit, size limit with and without a length, unsupported format, auth, budget, audit rows without content, logs free of audio/transcript/answer, voice prompt used only for voice, text `ask` unchanged |
| Client core | state sequence for a normal turn, indicator-before-microphone ordering, mute (no capture, no send, playback stopped, state visible), each failure path continues the loop, local fallback speech, conversation reuse and expiry |
| Client API | multipart contents, auth header, https enforcement, error-envelope mapping, token not in repr/logs |
| Client audio | WAV encoding, silence cutoff logic, token store behavior with fakes |
| Regression | all M1–M4 tests unchanged |
| Opt-in live | real speech round trip with a key (skipped otherwise) |

## 9. Risks

- **Privacy of voice:** always-on listening is not implemented (push-to-talk default); capture is indicated before it starts; audio is never stored; mute is enforced in the core, not just the UI.
- **Provider contract drift:** the HTTP contract was checked against current OpenAPI examples and a compatible provider's documentation (`file`, `model`, `text`; `model`, `input`, `voice`); the speech `response_format` field and default models rest on prior knowledge and are covered only by the opt-in live test.
- **Unverified hardware adapters:** microphone, playback, `say`, Keychain, and any wake-word engine need a Mac with devices; the tested core isolates them behind interfaces.
- **Cost:** per-user daily limit, upload and reply caps, existing token budget.
- **Untrusted audio:** format sniffing, size limits, no decoding on the server (audio is forwarded to the provider only).

## 10. Implementation order (each step ends green)

1. Settings, domain errors, audio sniffing, speakable text, voice prompt, per-path body limits.
2. Speech adapters and fakes.
3. `VoiceService` and the voice endpoint.
4. Mac client core, API client, adapters, entry point.
5. Docs, compose/env, mutation checks, Docker walkthrough, commit, CI.

## 11. Implementation notes, additions, and what was not verified

**Additions and refinements**
- **Streaming body limit for every endpoint:** a pure-ASGI middleware counts streamed bytes, so chunked uploads without a `Content-Length` are bounded too (previously only the header was checked, a limitation noted in Milestone 1). On overflow it answers `413 PAYLOAD_TOO_LARGE` itself and shows the app a disconnect, because FastAPI otherwise converts any body-read exception into a generic `400`. Per-path limits keep 64 KB for ordinary endpoints and allow the voice upload size.
- The conversation is ownership-checked **before** transcription so a request naming someone else's conversation fails without a paid call.
- Every paid attempt is counted toward the daily voice limit through an `assistant.voice.attempt` audit row written (and committed) before the speech call, so failed attempts consume quota; requests rejected for format or size do not.
- The client's trigger source is polled even while muted (the keyboard must be able to unmute) but is told `muted=True` and must not capture audio then; the core separately guarantees the microphone is never opened and nothing is sent while muted. Tokens are stored through the optional `keyring` package rather than the `security` command line, which would expose the token in the process list.

**Verified:** audio sniffing against spoofed types and containers; speakable-text preparation; speech base-URL rules; both speech adapters against a mock transport (request shape, auth, multipart fields, status and transport error mapping, malformed bodies, size cap, no redirects, no leakage of provider bodies or keys); the voice API end to end with fake providers against real Postgres (storage of text only, ownership, validation, size limits including chunked uploads, STT and TTS failure paths, daily limit, token budget, audit and log privacy, voice style only for voice); the client core (state sequence, indicator-before-capture ordering, mute guarantees including mute during recording, waiting and playback, every failure path and error-message mapping, local fallback speech, conversation reuse and expiry); the client API, audio helpers, silence detection, and token stores; and mutation checks showing the suite fails when each safeguard is removed.

**Not verified:** a live speech round trip (no key; `tests/test_speech_live.py` is opt-in), the speech `response_format` request field and default model names (these rest on prior knowledge, while `file`/`model`/`text` and `model`/`input`/`voice` were confirmed against OpenAPI examples and a compatible provider's documentation), the macOS adapters (`sounddevice` microphone, `afplay`, `say`, Keychain through `keyring`), and a "Hey Friday" wake word (needs a custom-trained model; push-to-talk is the working default).
