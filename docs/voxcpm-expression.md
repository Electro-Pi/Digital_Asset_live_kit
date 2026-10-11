# VoxCPM2 conversational delivery

## Streaming PCM phrase mode

Adapted from the user's `tts_mulitple_concat.py`, while keeping HTTP audio
streaming. Enable with `TTS_PHRASE_STREAMING=1` and leave `TTS_STYLE` empty.
The code default is off. This deployment now sets `TTS_PHRASE_STREAMING=1`
at the user's request, with `TTS_STYLE` empty.

`ConcatPhraseTokenizer` releases complete sentences after at least four
words; commas, colons, semicolons and ellipses stay within the same generation
to reduce mid-sentence voice restarts. Short openings stay attached to the next
sentence. EOF releases all remaining words, including short final questions.
Decimal prices are preserved. Every request still sends
`response_format=pcm`, `stream=true`, and `stream_format=audio`.

Audio uses one fixed output gain (`TTS_GAIN_DB=4.0`) with peak limiting.
The earlier per-phrase gain estimate has been removed: a quiet first 240 ms
could cause loud later words to be amplified. Model-generated volume variation
can still occur; this change removes the extra variation introduced locally.
The onset fades over 10 ms and the existing tail fades over 5 ms. A bounded tail
buffer tops up near-digital silence to the punctuation pause: comma 180 ms,
semicolon 260 ms, full stop 340 ms, exclamation 400 ms, question 420 ms.
Existing pauses are not shortened, and quiet word endings are not removed by
relative-energy trimming. These are minimum audio pauses, not guarantees about
wall-clock playback gaps: a slow next request can still delay playback.

Only startup/tail audio is held (240 ms and at most 420 ms respectively), not
the whole phrase or reply. The LiveKit adapter synthesizes phrases in order and
can start the next request while already-generated audio is playing. Each
phrase is still an independent model generation, so listening is necessary to
check voice continuity. Keep the user's original standalone script intact.

Run `.venv/bin/python scripts/audition_voxcpm.py --chunks` for the A/B comparison.
Both variants use streaming PCM; WAV files are merely offline playback exports.
Latest: [streamed PCM comparison](../data/tts-auditions/20260920-125612/index.html).
A local HTTP regression deliberately holds the response open and verifies that
LiveKit receives audio before HTTP EOF. Regression checks also cover incremental
sentence release, short final questions, and preserving descriptive Arabic
capability statements while blocking explicit waiting messages.

## Incomplete playback investigation (2026-09-20)

The speech filter now preserves descriptions such as “أدور لك أحسن الوجبات حسب
مزاجك” and “في البداية، أدور لك على الوجبات حسب ذوقك”. Explicit waits still
trigger protection; a bare unfulfilled lookup promise remains blocked.

At 15:50:54 UTC a client-initiated participant disconnect interrupted speech
after the complete last sentence had already been synthesized. No automatic
hang-up timer was found in the local frontend. Backend disconnect logging now
includes the agent state and active speech ID for correlation. Normal disconnect
handling remains enabled: keeping an agent alive after the listener leaves
cannot restore playback. The reason for the client leaving remains unverified;
these backend changes do not claim to fix unexpected client disconnections.

## Arabic punctuation audition (2026-09-20, 12:44 UTC)

After the user rejected the short English style prompt, `TTS_STYLE` was cleared
again and the backend restarted and verified healthy. No experimental punctuation
or vowel-removal transform has been enabled in production.

Run `.venv/bin/python scripts/audition_voxcpm.py --punctuation` to compare:

- A: production text cleanup with no style instruction.
- B: connected phrases using commas instead of internal full stops.
- C: firmer phrase boundaries using full stops instead of commas.
- D: B with short-vowel marks removed, retaining shadda and base letters.

The experiments run after production cleanup so it cannot undo the comparison.
All use the configured voice, seed, speed, and gain. Every output includes the
exact text, duration, and audio hash in `manifest.json`. Listen before selecting
a default; changes in duration alone do not prove naturalness or completeness.

Latest comparison: [12 audio samples](../data/tts-auditions/20260920-124430/index.html).
All clips generated (3.07–5.78 seconds). No subjective quality claim has been
validated. D tests a separate hypothesis about vowel density and needs particular
attention to pronunciation.

## Deployment result (2026-09-20)

The long style instruction below produced truncated audition clips. A shorter
`Warm, cheerful tone` instruction produced clips of plausible duration, but the
user reported stuttering after deployment. `TTS_STYLE` was therefore cleared to
restore baseline synthesis. Neither clip duration nor passing integration tests
establishes audible quality or hosted-server control-prompt compatibility. Keep
style control disabled pending verification of the server implementation and
listening tests. The exact cause of the stutter is not yet confirmed.

The client already synthesizes whole replies and uses native playback speed
(`TTS_SPEED=1.0`). Changing playback speed or output gain cannot create expressive
intonation. The remaining delivery controls are model conditioning, reference
performance, and the actual spoken text.

## Style control

The official VoxCPM2 API supports a parenthesized instruction at the start of
`text`, including with reference-based controllable cloning:
https://github.com/OpenBMB/VoxCPM#-controllable-voice-cloning

The agent now accepts this optional configuration:

```dotenv
TTS_STYLE="Warm, friendly conversational delivery, relaxed natural pace, varied intonation, gentle emphasis on meaningful words"
```

The plugin adds `(instruction)` to `input` after Arabic/food-name cleanup, so the
speech filters cannot remove its parentheses. Empty speech is still skipped.
Leave this unset to preserve existing behavior. Do not include parentheses in
the setting itself. This is a global delivery instruction for both voice profiles;
keep it independent of gender and put Saudi/Najdi wording in the spoken text.

Compatibility must be checked on the actual server: on 2026-09-20 the configured
hosted gateway returned 404 for both `/openapi.json` and `/v1/audio/voices`.
A successful speech HTTP response alone does not prove the instruction was
interpreted. Compare the same short Arabic utterances with and without the
prefix, keeping voice and seed fixed. Reject it if the instruction is spoken,
voice identity drifts, or pronunciation gets worse. Do not enable globally until
this listening comparison passes. See the deployment result above for current status.

## Reference performance

For continuation cloning, use a clean recording of the intended speaker actually
speaking conversational Najdi Arabic, with the rhythm and emotion you want.
A flat reading sample may transfer its flat delivery. Provide its exact transcript:

```dotenv
TTS_REF_AUDIO="<reference location accessible to the TTS server>"
TTS_REF_TEXT="<exact words spoken in that recording>"
```

These overrides apply to the female profile; the male profile retains its own
reference and transcript. The client sends `ref_audio` and `ref_text`, but their
mapping to VoxCPM2 reference/continuation modes must be confirmed in the server.
For upstream VoxCPM2, `reference_wav_path` is the controllable cloning input;
`prompt_wav_path` plus `prompt_text` supplies continuation conditioning. A named
voice's server-side cache may need rebuilding to change its reference performance.

## Listening comparison

Use a greeting, recommendation, question, and order confirmation. Keep the words,
seed, volume, and voice fixed while comparing baseline versus style conditioning.
Then separately compare references. Listen for connected phrasing, appropriate
question intonation, natural emphasis, preserved words, and the intended voice.
Repeat on several seeds before choosing a default. Also compare current tashkeel
against lighter diacritization, but preserve established pronunciation corrections
unless listening proves the alternative better. Pitch averages and unit tests
cannot establish human naturalness.
