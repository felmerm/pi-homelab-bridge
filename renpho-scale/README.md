# Renpho scale pipeline

Reads supported QN-format Bluetooth measurements, reacts at the first stable
weight, and persists final readings for Google Sheets delivery. Raw packets and
decoded readings remain in private Pi files; the public repository contains
only example configuration and synthetic test data.

See [project setup](../docs/SETUP.md). Fill in your own scale MAC, spreadsheet
ID, timezone, and private credential paths in the example config. Source uses
the pinned MIT-licensed `renpho-escs20m` library; its notice is retained under
`licenses/`. Hardware compatibility requires a real device check.

The sample catalog has 8 good and 12 bad responses with a demo cutoff of 100 lb.
All audio compatibility names play the project's generated test tone. Replace
them with permitted public clips or choose No audio. The special lighting scene
is disabled. Meme mode is intended for a consensual private joke; the scale does
not identify who is standing on it.
