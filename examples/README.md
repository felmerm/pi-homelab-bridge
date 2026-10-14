# Public demo assets

`tone.mp3` is a generated 440 Hz sine wave lasting about 0.6 seconds, encoded at
24 kHz, 48 kbps stereo MPEG-2 Layer III. It contains no recording, metadata,
speech, personal information, or third-party meme audio. It is covered by the
project's MIT license.

It was generated with FFmpeg:

```sh
ffmpeg -f lavfi -i 'sine=frequency=440:duration=0.6' -ac 2 -ar 24000 -b:a 48k -codec:a libmp3lame -write_xing 0 -map_metadata -1 tone.mp3
```

Sample sound names are compatibility labels; they all point to this tone.
