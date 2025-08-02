BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "links" (
	"source"	INTEGER NOT NULL,
	"destination"	INTEGER NOT NULL,
	"snr"	REAL DEFAULT -500,
	"seen"	INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY("source","destination")
);
CREATE TABLE IF NOT EXISTS "nodes" (
	"id"	INTEGER NOT NULL,
	"shortname"	TEXT,
	"longname"	TEXT,
	"seen"	INTEGER,
	"latitude"	REAL,
	"longitude"	REAL,
	"tracestart"	INTEGER DEFAULT 0,
	"role"	INTEGER DEFAULT 0,
	"hardware"	INTEGER DEFAULT 0,
	PRIMARY KEY("id")
);
CREATE TABLE IF NOT EXISTS "packet_types" (
	"port_num"	INTEGER,
	"port_name"	TEXT,
	PRIMARY KEY("port_num")
);
CREATE TABLE IF NOT EXISTS "packets" (
	"source"	INTEGER,
	"type"	INTEGER,
	"time"	INTEGER
);
INSERT OR REPLACE INTO "packet_types" VALUES (0,'Unkown');
INSERT OR REPLACE INTO "packet_types" VALUES (1,'Text');
INSERT OR REPLACE INTO "packet_types" VALUES (2,'Remote Hardware');
INSERT OR REPLACE INTO "packet_types" VALUES (3,'Position');
INSERT OR REPLACE INTO "packet_types" VALUES (4,'Node Info');
INSERT OR REPLACE INTO "packet_types" VALUES (5,'Routing');
INSERT OR REPLACE INTO "packet_types" VALUES (6,'Admin');
INSERT OR REPLACE INTO "packet_types" VALUES (7,'Text Message Compressed');
INSERT OR REPLACE INTO "packet_types" VALUES (8,'Waypoint');
INSERT OR REPLACE INTO "packet_types" VALUES (9,'Audio');
INSERT OR REPLACE INTO "packet_types" VALUES (10,'Detection Sensor');
INSERT OR REPLACE INTO "packet_types" VALUES (11,'Alert');
INSERT OR REPLACE INTO "packet_types" VALUES (12,'Key Verification');
INSERT OR REPLACE INTO "packet_types" VALUES (32,'Reply');
INSERT OR REPLACE INTO "packet_types" VALUES (33,'IP Tunnel');
INSERT OR REPLACE INTO "packet_types" VALUES (34,'Pax Counter');
INSERT OR REPLACE INTO "packet_types" VALUES (64,'Serial');
INSERT OR REPLACE INTO "packet_types" VALUES (65,'Store Forward');
INSERT OR REPLACE INTO "packet_types" VALUES (66,'Range Test');
INSERT OR REPLACE INTO "packet_types" VALUES (67,'Telemetry');
INSERT OR REPLACE INTO "packet_types" VALUES (68,'ZPS');
INSERT OR REPLACE INTO "packet_types" VALUES (69,'Simulator');
INSERT OR REPLACE INTO "packet_types" VALUES (70,'Traceroute');
INSERT OR REPLACE INTO "packet_types" VALUES (71,'Neighbor Info');
INSERT OR REPLACE INTO "packet_types" VALUES (72,'ATAK Plugin');
INSERT OR REPLACE INTO "packet_types" VALUES (73,'Map Report');
INSERT OR REPLACE INTO "packet_types" VALUES (74,'Power Stress');
INSERT OR REPLACE INTO "packet_types" VALUES (76,'Reticulum Tunnel');
INSERT OR REPLACE INTO "packet_types" VALUES (77,'Cayenne');
INSERT OR REPLACE INTO "packet_types" VALUES (256,'Private');
INSERT OR REPLACE INTO "packet_types" VALUES (257,'ATAK Forwarder');
INSERT OR REPLACE INTO "packet_types" VALUES (512,'Device Telemetry');
INSERT OR REPLACE INTO "packet_types" VALUES (513,'Power Telemetry');
INSERT OR REPLACE INTO "packet_types" VALUES (514,'Environment Telemetry');
INSERT OR REPLACE INTO "packet_types" VALUES (515,'Host Metrics');
INSERT OR REPLACE INTO "packet_types" VALUES (516,'Air Quality');
INSERT OR REPLACE INTO "packet_types" VALUES (517,'Health Telemetry');
CREATE VIEW ViewPackets AS
SELECT source, longname, type, port_name, time, role FROM packets AS p
INNER JOIN packet_types ON packet_types.port_num = p.type
INNER JOIN nodes ON nodes.id = p.source;
CREATE TRIGGER delete_old_links
AFTER INSERT ON links
BEGIN
    DELETE FROM links
    WHERE seen < (strftime('%s', 'now') - 86400);
END;
CREATE TRIGGER delete_old_packets
AFTER INSERT ON packets
BEGIN
    DELETE FROM packets
    WHERE time < (strftime('%s', 'now') - 604800);
END;
COMMIT;
