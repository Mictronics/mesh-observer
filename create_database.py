# This file is part of Meshtastic mesh observer.
#
# Copyright (c) 2025 Michael Wolf <michael@mictronics.de>
#
# Mesh observer is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# Mesh observer is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with mesh observer. If not, see http://www.gnu.org/licenses/.
#
import sqlite3

# Source files
sql_file = "network.sqlite3.sql"
db_file = "network.sqlite3"

# Read SQL file
with open(sql_file, "r", encoding="utf-8") as f:
    sql_script = f.read()

# Connect to sqlite database
# If the database file does not exist, it will be created
conn = sqlite3.connect(db_file)
cursor = conn.cursor()

# Execute SQL script
# This will create tables and insert data as defined in the SQL file
cursor.executescript(sql_script)

# Save (commit) the changes
conn.commit()
conn.close()

print("Database successfully created.")
