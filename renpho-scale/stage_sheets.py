"""Install the service/config without starting it or creating Google keys."""
from pathlib import Path
import pwd
import subprocess


def command(*args):
    subprocess.run(args, check=True)


try:
    pwd.getpwnam("renpho-scale")
except KeyError:
    command("useradd", "--system", "--user-group", "--home-dir", "/var/lib/renpho-scale",
            "--shell", "/usr/sbin/nologin", "renpho-scale")
command("install", "-d", "-o", "root", "-g", "renpho-scale", "-m", "750", "/etc/renpho-scale")
command("install", "-d", "-o", "renpho-scale", "-g", "renpho-scale", "-m", "700", "/var/lib/renpho-scale")
if not Path("/etc/renpho-scale/sheets.json").exists():
    command("install", "-o", "root", "-g", "renpho-scale", "-m", "640",
            "/tmp/sheets.example.json", "/etc/renpho-scale/sheets.json")
command("install", "-o", "root", "-g", "root", "-m", "644",
        "/tmp/renpho-sheets.service", "/etc/systemd/system/renpho-sheets.service")
command("systemctl", "daemon-reload")
print("Service staged; not enabled or started. Google key still required.")
