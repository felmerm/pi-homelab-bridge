# Bridge actions

The bridge accepts typed actions through one authenticated queue. `actions.py`
validates the inputs; `bridge.py` owns transport, deduplication and scheduling;
`alexa_direct.py` owns Amazon operations. New capabilities belong in these layers,
rather than separate servers or copied authentication code.

## HTTPS

POST to `https://raspberrypi.local:8765/actions` with the existing bearer token
and a JSON body. Verify the Pi's certificate as existing clients do.

Announcement (used by iPhone messages and plain SSH text):

```json
{"action":"announce","message":"GG freaking easy"}
```

Direct speech without the announcement experience:

```json
{"action":"speak","message":"GG freaking easy"}
```

MP3 with optional speech before/after:

```json
{"action":"audio","url":"https://your-audio-host.example/roast.mp3","before":"Listen up.","after":"Okay then."}
```

Add `"dry_run":true` to validate silently. A dry run does not fetch/validate the
remote file, contact Amazon, or prove playback. HTTP 202 means queued; HTTP 200
may mean a duplicate within ten seconds. Unsupported actions/fields return 400.
Existing POST `/notify` clients and their app/title/message fields still work.

The URL must use HTTPS, an internet hostname, port 443, no embedded credentials
or fragment, and at most 2,048 characters. Signed URL queries are supported and
escaped correctly for XML. The bridge never downloads URLs or logs them.
Hostname checks reject common LAN names and IP literals, but do not prove DNS
resolves publicly; callers must supply a genuinely internet-accessible endpoint.
Optional before/after speech totals at most 1,500 characters. It is escaped as
plain text, so callers cannot inject SSML. Arbitrary SSML/Amazon payloads are
not exposed.

## iPhone Shortcuts / restricted SSH

Keep **Alexa Say** unchanged for ordinary text. To create **Alexa Audio**, use
the same Run Script over SSH settings, with this Text passed as its Script:

```text
bridge-action:{"action":"audio","url":"https://your-audio-host.example/roast.mp3"}
```

Build JSON with Shortcuts' Dictionary/JSON tools when inserting variables;
concatenating unescaped text into JSON can produce invalid input. The reserved
`bridge-action:` prefix selects the structured API; it is never shell execution.
`bridge-dry-run:Hello` and ordinary text retain their previous behavior.

## Audio backend and limits

Audio is submitted as a generated SSML `<speak><audio src="..."/></speak>`
`Alexa.Speak` operation through the existing unofficial Amazon `/api/behaviors/preview`
route, saved session, selected Echo, and pinned aioamazondevices client.
No Bluetooth, Home Assistant, extra Python dependency, new Amazon skill, or
media hosting service is installed. This is short announcement audio, not an
AudioPlayer music stream with seek/pause/queue controls. The original SSML
`AlexaAnnouncement` test spoke its surrounding text but skipped the MP3 on this
Echo. iPhone `/notify` and `announce` actions use Amazon Announcement. Scale text uses
`speak`, and scale MP3 reactions use `audio`; both select Alexa.Speak. All routes
explicitly target only the configured Echo. `announced` counts Announcement
submissions, and `speak_submitted` counts plain Speak submissions.

**Verified on Felm's Echo on 2026-10-07:** Felm heard the short MP3 tuning tone
through the Speak route. All 17 regression tests passed on the Pi, and both
legacy and structured SSH dry runs passed. Future API acceptance still does not
prove an individual file played: an Echo can reject/skip media after HTTP
acceptance. `audio_submitted` counts upstream acceptance only; `announced`
retains its meaning for text requests. This unofficial interface can change.

Amazon documents these SSML media requirements for skills; they are the format
guidance for our audio route, not a guarantee that the
unofficial endpoint supports every skill feature:

- Internet-accessible HTTPS with a trusted certificate; self-signed Pi TLS and
  LAN-only files are unsuitable.
- MPEG-2 MP3, 48 kbps, 16,000 / 22,050 / 24,000 Hz; maximum 240 seconds.
- Audio must not contain customer-specific or sensitive information. Keep scale
  weights in normal text speech; host generic roast/sound clips separately.

For an existing MP3, convert on a machine with FFmpeg:

```sh
ffmpeg -i input.mp3 -ac 2 -codec:a libmp3lame -b:a 48k -ar 24000 -write_xing 0 output.mp3
```

No upload or hosting is included. Amazon receives the URL and fetches the media;
its host may receive Amazon's fetch request. Never include bridge/Amazon secrets
in audio URLs. Generic media can use an expiring signed URL.

The shared queue holds 20 requests. One worker submits them serially and pauses
two seconds after each attempt; it does not wait for playback to finish, so
rapid requests may interrupt audio. Failed sends are not automatically replayed.
The scale uses an editable speech/media reaction catalog; see the scale docs.

## Local lights, modes, and grouped actions

`lights` uses direct local Hue HTTPS after linking, not Alexa command
interpretation. Unlinked Hue returns `503 hue_not_linked`; it does not fall back
through Amazon. Existing target/color JSON remains unchanged.

```json
{"action":"lights","target":"all lights","color":"red"}
```

`sequence` groups 1–5 validated playback/light actions. The Pi executes local
lighting steps and submits adjacent speech/media as Amazon serial batches.
Nested groups/per-step dry runs are rejected. A mixed group is ordered but not
atomic and does not guarantee physical completion. The special scene stays paused.

`mode` is a synchronous local control (no Amazon/queue dependency):

```json
{"action":"mode","name":"meme","enabled":false}
```

Use true to resume. Authenticated `GET /modes` reads state. Mode actions cannot
be nested. Dry runs have no persistent effect. See [controls and Hue linking](CONTROLS.md)
for storage, local TLS, deployment, and the future Alexa input boundary.

## Verification and extension

`test_bridge.py` covers both transports, validation, XML escaping, deduplication,
and actual Amazon request submission using fake HTTP. `test_audio_live.py` sends
one public short tuning-tone MP3; listen for speech, a tone, then speech. Its
fixture is [miso-develop/tuner](https://github.com/miso-develop/tuner); no fixture
file is copied into our project.

For a new action: add a validated `Action` variant/parser branch, adapter dispatch,
meaningful submission/validation tests, then document the JSON contract here.
Preserve legacy `/notify` and plain SSH text, reuse session persistence and the
shared queue, and keep raw commands/credentials out of logs.

References: [Amazon SSML audio requirements](https://developer.amazon.com/en-US/docs/alexa/custom-skills/speech-synthesis-markup-language-ssml-reference.html#audio)
(section “audio”); [alexa-remote SSML sequence implementation](https://github.com/Apollon77/alexa-remote/blob/master/alexa-remote.js).
