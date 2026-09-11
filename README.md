# Meshtastic Observer

A Python script that connects to a Meshtastic node's TCP API, logs the mesh traffic it sees into
a SQLite database, and generates statistics/network-graph web content from it -- the same job it
always did. Since firmware 2.8.x removed MQTT JSON publishing, this is now also the only client
allowed to hold that node's TCP connection (the node's API accepts one client at a time), so it
additionally repeats that connection out over both the TCP and HTTP Meshtastic API surfaces to any
other client that needs live mesh data (`meshtastic2hass`, `meshtastic-powered-vue`, ...). This is
a deliberate exception to the project's earlier "never uses the Meshtastic API" design -- see
CLAUDE.md.

## Installation

Run the following commands for repository cloning, creation of a Python virtual environment and installation of dependencies.

```bash
# Clone repository
git clone https://github.com/Mictronics/mesh-observer.git

# Create Python virtual environment
cd mesh-observer
python -m venv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Create database
```bash
# Run the following command
python3 create_database.py

# This will create an empty sqlite3 database from network.sqlite3.sql
```

## Create FTP credentials

FTP upload is optional. The script will upload the generated web content automatically to a
remote web server only if `ftp_credentials.py` exists; if it doesn't, FTP upload is skipped and
the failure is logged, nothing else needs to be changed in the code.

To enable it, create a Python file named `ftp_credentials.py` with the following content.
```python
import os

__hostname__ = "ftp server domain"
__username__ = "ftp username"
__password__ = "ftp password"
__local_folder__ = os.getcwd() + "/web"
__remote_folder__ = "/"

# Change above credentials and remote folder as required.
```

## Create TCP credentials

Create a Python file named `tcp_credentials.py` pointing at the Meshtastic node's TCP API, and
picking the ports this app's own repeater binds to for other Meshtastic clients:
```python
__hostname__ = "192.168.1.100"  # the node's IP or hostname
__port__ = 4403                 # the node's real TCP API port

__repeater_tcp_port__ = 4403    # this app's own TCP repeater (meshtastic2hass, etc.)
__repeater_http_port__ = 4404   # this app's own HTTP repeater (meshtastic-powered-vue, etc.)

# __repeater_http_port__ defaults away from 80 so the service doesn't need
# root/CAP_NET_BIND_SERVICE -- point browser clients at "<this-host>:4404",
# not a bare hostname.
```

Only one client can hold the node's own TCP API connection at a time -- this app is meant to be
that one client. Anything else that needs live Meshtastic data (an app using
`meshtastic.tcp_interface.TCPInterface`, or a browser app using `@meshtastic/transport-http`)
should point at *this app's* repeater ports instead of the node directly.

## Run script manually
```bash
# In mesh-observer folder: activate the virtual environment
source .venv/bin/activate

# Connects to the node (tcp_credentials.py above), writes to the database, and
# serves both repeater ports for other Meshtastic clients until stopped
python3 meshtastic_observer.py

# One-shot: regenerate the network graph (web/visualization.html) from the
# database and exit, without connecting to the node
python3 meshtastic_observer.py -g

# One-shot: regenerate the statistics site (web/index.html and charts) from
# the database and exit
python3 meshtastic_observer.py -s
```

## Run tests

A small `pytest` suite under `tests/` regression-tests the packet-to-database-write logic
against synthetic packets shaped like real decoded Meshtastic traffic.
```bash
source .venv/bin/activate
pytest tests/
```

## Create systemd service
```bash
# Open meshobserver.service and change the folder names to your repository clone location
# Copy the service file
sudo cp meshobserver.service /usr/lib/systemd/system/meshobserver.service

# Reload systemd services
sudo systemctl daemon-reload

# Enable and run service
sudo systemctl enable --now meshobserver.service

# Check service status
sudo systemctl status meshobserver.service

# Check debug log of service
sudo journalctl -u meshobserver.service -f
```

## Customization

You may change the textual content in _index.html.j2_ in case you are running this script for your local mesh and publish the web content.

## Web content output

Web content will be generated in the _./web_ sub-folder. Statistical graph for each node in _./web/images_. Open the generated _index.html_ in browser.

## npm package
No, I will not create an npm package for this.

This is some pragmatic, individual code for personal use. Feel free to create pull requests in case you want to change or improve the sourcecode. Contribution appreciated.
