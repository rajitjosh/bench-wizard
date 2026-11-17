"""Set the port of the Procfile's web line: python3 procfile_port.py PORT, run inside a bench folder.

Leaves the rest of the Procfile as it is; `bench setup procfile` would ask before overwriting it and drop
any lines added by hand. Standalone, so it runs without the wizard's package on the path.
"""
import os
import re
import sys

if __name__ == "__main__":
    port = str(int(sys.argv[1]))
    if os.path.exists("Procfile"):
        with open("Procfile") as fh:
            text = fh.read()
        text = re.sub(r"^(web:.*?)(?: --port \d+)?[ \t]*$", lambda m: f"{m.group(1)} --port {port}", text, flags=re.M)
        with open("Procfile", "w") as fh:
            fh.write(text)
