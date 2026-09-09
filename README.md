# Meshtastic Observer

A Python script that is monitoring a (via serial interface) connected Meshtastic node or a local running, native meshtasticd service through the system journal.

The monitoring is purely passive by line parsing the nodes debug log. No Meshtastic API is used and no packets are transmitted.

## Installation

Run the following commands for repository cloning, creation of a Python virtual environment and installation of dependencies.

```bash
# Install dependencies
sudo apt install libsystemd-journal-dev

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

## Create MQTT credentials

MQTT is an alternative to the serial/journal readers: it subscribes to a broker publishing
Meshtastic JSON packets (e.g. `msh/2/json/#`) instead of tailing a local node's debug log.
Create a Python file named `mqtt_credentials.py` with the following content.
```python
__broker__ = "mqtt broker hostname"
__port__ = 1883
__username__ = "mqtt username"
__password__ = "mqtt password"
__topic__ = "msh/2/json/#"

# Change above credentials, port and topic filter as required.
```

## Run script manually
```bash
# In mesh-observer folder: activate the virtual environment
source .venv/bin/activate

# Run the script for a Meshtastic device connected via serial interface (USB)
# Change the ttyUSB0 for your connected serial device
python3 meshtastic_observer.py --dev /dev/ttyUSB0

# Run the script for a locally running meshtasticd service
# meshtasticd service needs to be configured for logging up to level debug
python3 meshtastic_observer.py

# Run the script against an MQTT broker instead (see mqtt_credentials.py above)
python3 meshtastic_observer.py --mqtt

# One-shot: regenerate the network graph (web/visualization.html) from the
# database and exit, without connecting to a device or journal
python3 meshtastic_observer.py -g

# One-shot: regenerate the statistics site (web/index.html and charts) from
# the database and exit
python3 meshtastic_observer.py -s
```

## Run tests

A small `pytest` suite under `tests/` regression-tests the regex-based debug log parsing
against real captured log lines.
```bash
source .venv/bin/activate
pytest tests/
```

## Configuration

Configuration of the systemd journal and meshtasticd native service is required when running on Linux.

meshtasticd native service will create a hugh journal log when the debug level is configured. Therefore the journal will be stored in memory and size limited to avoid excessive disk usage.

```bash
# Open the journal daemon configuration
sudo nano /etc/systemd/journald.conf

# Uncomment and change at least the following two lines
# Journal is stored in volatile memory
# Limit size to 48 MByte during runtime
[Journal]
Storage=volatile
RuntimeMaxUse=48M

# Adjust limit as desired
```

Activate the debug level log in journal for meshtasticd native service.
```bash
# Open meschtasticd configuration
sudo nano /etc/meshtasticd/config.yaml

# Set log level to debug
Logging:
  LogLevel: debug # debug, info, warn, error
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
