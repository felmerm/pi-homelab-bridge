# Lightweight Pi diagnostics

Low-priority health snapshots run every two minutes. Persistent journal
configuration and a PC reachability recorder help investigate power, OOM,
storage, Wi-Fi, and SSH outages. A missed connection alone does not prove a crash.

Snapshots/reports are private runtime data, ignored by Git; the general journal
may contain service-generated information. The tools do not read Amazon state,
Google keys, notification contents, DNS queries, or raw weight data.

Edit the example SSH host/user/key and LAN range in the PowerShell wrappers and
monitor before installation. The installer checks tests and backs up touched
files. Review these scripts before applying them to your own host. A sudden
power cut can lose the last records; logging is not a guarantee of diagnosis.

See [project setup](../docs/SETUP.md) and [security](../SECURITY.md).
