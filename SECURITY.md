# Security boundaries

This is an experimental personal homelab application, not a formally audited
security product. Keep the bridge on a trusted LAN; do not port-forward it or
the dashboard. The dashboard intentionally binds to loopback, checks Host and
Origin, restricts served files, and requires explicit same-origin mutations.

Amazon browser linking opens the real HTTPS Amazon sign-in page. Password and
2FA are entered there, not stored by this project. The returned authorization
callback is sensitive and must only be pasted into the local linking terminal.
Reusable session tokens are stored on the Pi and carry broader account authority
than an announce-only permission. Treat that file like a password.

Production secrets belong outside Git under protected `/etc` or `/var/lib`
paths. Runtime Amazon state is mode 600 in a mode 700 directory. Google keys,
bridge tokens, and Hue credentials need similarly restricted ownership. The
administrator's sudo access remains privileged and can access those secrets.

Restricted iPhone SSH uses a forced receiver that interprets input as text or
validated JSON, never arbitrary shell commands. It has no interactive shell
session, forwarding, or access to the Amazon token file. Verify SSH host keys;
never use `StrictHostKeyChecking=no` to work around a connection issue.

Audio URLs must be public HTTPS endpoints. Link import validates DNS answers and
redirects, rejects private addresses, bounds size/time, and pins the connection
to validated addresses. Speech is escaped before SSML construction. Audio
publication is an explicit user action and requires a configured owned public
repository; it is not a default side effect of uploading a file.

Weight databases, raw scale packets, Google spreadsheet IDs, screenshots, and
backups may contain personal information. `.gitignore` is defense in depth;
inspect the exact staged tree and scan both files and Git history before a push.

If a secret is ever published, revoke/rotate it first. Deleting the file from the
latest commit does not remove it from history or other people's copies. Report
security problems without attaching tokens, private logs, or login callbacks.
