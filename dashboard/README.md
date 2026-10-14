# Homelab dashboard

Standard-library Python serves HTML/CSS/JavaScript at `http://127.0.0.1:8787/`.
Status and weight helpers run over pinned administrator SSH; secrets stay on
the Pi. Weight history is personal data and is cached privately on the PC.

Set the `HOMELAB_*` environment values described in [setup](../docs/SETUP.md),
then start with `Start-Dashboard.ps1`; stop with `Stop-Dashboard.ps1`. Host/Origin
checks and an explicit file allowlist protect local routes. Do not change the
binding to make the dashboard public.

Meme Scale has editable grouped cards, inline validation, optional audio,
explicit Echo tests, and explicit catalog saves. Weight events update the page
without resetting unsaved drafts. The editor does not save changes during a
playback test.

Custom audio processing stays local until an explicit Publish action. Publishing
is disabled unless you set `HOMELAB_AUDIO_REPOSITORY` to your own public repo;
FFmpeg and an authenticated GitHub CLI are needed for that optional workflow.
Static Pi-hole/NAS links are example hostnames, and sheet links open generic
Google Sheets until customized locally.
