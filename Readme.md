# IoT → MQTT → Kafka → ClickHouse

This document is a **complete, step‑by‑step plan** to build a local end‑to‑end pipeline where a phone (or any IoT device) publishes telemetry to **MQTT (Mosquitto)**, an **MQTT → Kafka bridge** forwards messages into **Kafka (Redpanda)**, and a **Kafka → ClickHouse consumer** writes structured events into **ClickHouse** for analytics.

It includes:

* Docker Compose for the core services (Mosquitto, Redpanda, ClickHouse, Kafka UI)
* Example MQTT payload and topics
* A lightweight Python bridge (MQTT subscriber → Kafka producer)
* A Kafka consumer (Python) that inserts into ClickHouse
* ClickHouse table DDL and example queries
* Commands to run everything locally and test with a mobile MQTT app (e.g., IoT MQTT Panel / MQTT Dash)

---

## High-level architecture

Phone (IoT)  →  MQTT broker (Mosquitto)  →  mqtt-to-kafka bridge (Python)  →  Kafka (Redpanda)  →  kafka-to-clickhouse consumer (Python)  →  ClickHouse

---

## Prerequisites

* Docker & Docker Compose installed
* Python 3.9+ for running example bridge/consumer scripts
* On your phone: an MQTT client app (IoT MQTT Panel or MQTT Dash) or a small Termux script

---

## 1) Docker Compose (quick local stack)

Create a file `docker-compose.yml` in a project directory with the following content:

```yaml
version: '3.8'
services:
  mosquitto:
    image: eclipse-mosquitto:2.0
    container_name: mosquitto
    ports:
      - '1883:1883'
      - '9001:9001' # websocket (optional)
    volumes:
      - ./mosquitto/config:/mosquitto/config:ro
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log

  redpanda:
    image: vectorized/redpanda:latest
    container_name: redpanda
    command: ["redpanda", "start", "--overprovisioned", "--smp", "1", "--memory", "1G", "--reserve-memory", "0M", "--node-id", "0", "--check=false"]
    ports:
      - '9092:9092'
      - '8082:8082' # Admin / REST (if needed)
    environment:
      - REDPANDA_AUTO_CREATE_TOPICS=true

  clickhouse:
    image: yandex/clickhouse-server:latest
    container_name: clickhouse
    ports:
      - '8123:8123'
      - '9000:9000'
    volumes:
      - ./clickhouse/config:/etc/clickhouse-server/config.d
      - ./clickhouse/data:/var/lib/clickhouse

  kafkacat-ui:
    image: obsidiandynamics/kafdrop:latest
    container_name: kafkacat_ui
    environment:
      - KAFKA_BROKERCONNECT=redpanda:9092
    ports:
      - '9000:9000'
    depends_on:
      - redpanda

networks:
  default:
    driver: bridge
```

Create a minimal Mosquitto config folder (`mosquitto/config/mosquitto.conf`) so Mosquitto won't fail on startup. Example `mosquitto/config/mosquitto.conf`:

```
listener 1883
allow_anonymous true
listener 9001
protocol websockets

log_type all
persistence true
persistence_location /mosquitto/data

# For production, configure authentication and TLS
```

Start the stack:

```bash
docker compose up -d
```

Check logs:

```bash
docker compose logs -f mosquitto
docker compose logs -f redpanda
docker compose logs -f clickhouse
```

---

## 2) Example MQTT topic and payload

We'll use topic `devices/{device_id}/telemetry` and JSON payloads like:

```json
{
  "device_id": "phone-123",
  "ts": 1699990000,
  "lat": 0.3476,
  "lon": 32.5825,
  "accel": { "x": -0.01, "y": 0.02, "z": 9.81 },
  "battery": 84
}
```

Use QoS 0 for simplicity while testing.

---

## 3) Publish from your phone (quick test)

Use **IoT MQTT Panel** or **MQTT Dash** on Android, or **MQTT Client (TuanPM)** on iOS.

* Broker: `<your-machine-ip>` (if phone on same Wi‑Fi) and port `1883`.
* Topic: `devices/phone-123/telemetry`
* Payload: paste the JSON above (update `ts` and coordinates)
* Publish manually or set up auto-publish for sensor fields if the app supports it.

If testing from the host machine, you can publish using `mosquitto_pub`:

```bash
# install mosquitto clients (Debian/Ubuntu): sudo apt install -y mosquitto-clients
mosquitto_pub -h localhost -p 1883 -t 'devices/phone-123/telemetry' -m '{"device_id":"phone-123","ts":'"$(date +%s)"',"lat":0.3476,"lon":32.5825,"battery":84}'
```

---

## 4) MQTT → Kafka bridge (Python)

We’ll run a small Python service that subscribes to MQTT and forwards messages into Kafka topics.

Create a virtualenv and install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install paho-mqtt kafka-python orjson
```

Save this as `mqtt_to_kafka.py`:

```python
# mqtt_to_kafka.py
import json
import os
from paho.mqtt.client import Client
from kafka import KafkaProducer
import time

MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'devices/+/telemetry')
KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP', 'localhost:9092')

producer = KafkaProducer(bootstrap_servers=[KAFKA_BOOTSTRAP],
                         value_serializer=lambda v: json.dumps(v).encode('utf-8'))


def on_connect(client, userdata, flags, rc):
    print('Connected to MQTT', rc)
    client.subscribe(MQTT_TOPIC)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode('utf-8')
        data = json.loads(payload)
    except Exception:
        # If payload is not JSON, forward as raw string
        data = {"raw": msg.payload.decode('utf-8')}

    # Example: publish to Kafka topic 'telemetry'
    # Optionally, include original topic
    kafka_value = {
        'topic': msg.topic,
        'payload': data,
        'received_at': int(time.time())
    }
    producer.send('telemetry', value=kafka_value)
    producer.flush()
    print('Forwarded message to Kafka')


if __name__ == '__main__':
    client = Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()
```

Run it (from the same machine that can reach Mosquitto & Redpanda):

```bash
export MQTT_BROKER=localhost
export MQTT_PORT=1883
export KAFKA_BOOTSTRAP=localhost:9092
python mqtt_to_kafka.py
```

Notes:

* For production, add reconnect logic, batching, backpressure, and error handling.
* If you prefer a connector-based approach, you can use a Kafka Connect MQTT Source connector — but local, easy development is often faster with a simple Python bridge.

---

## 5) Verify message in Kafka

Use `kafkacat` (or `kcat`) locally to read the `telemetry` topic (install `kcat`), or use the Kafdrop UI at `http://localhost:9000`.

```bash
# Install kcat (Debian/Ubuntu): sudo apt install kcat
kcat -b localhost:9092 -t telemetry -C -o beginning -q
```

You should see the JSON messages forwarded from MQTT.

---

## 6) Create ClickHouse table

Connect to ClickHouse HTTP interface (port 8123) using the client or curl. Example DDL — create a wide table suitable for JSON ingestion:

```sql
CREATE DATABASE IF NOT EXISTS iot;

CREATE TABLE IF NOT EXISTS iot.telemetry (
  device_id String,
  ts UInt32,
  lat Float64,
  lon Float64,
  battery UInt8,
  accel Nested(x Float32, y Float32, z Float32),
  topic String,
  received_at UInt32
) ENGINE = MergeTree()
ORDER BY (device_id, ts);
```

Run via `clickhouse-client` (if installed) or HTTP API:

```bash
# Using curl
curl -sS 'http://localhost:8123/' --data-binary $'CREATE DATABASE IF NOT EXISTS iot;'
curl -sS 'http://localhost:8123/' --data-binary $'CREATE TABLE IF NOT EXISTS iot.telemetry (device_id String, ts UInt32, lat Float64, lon Float64, battery UInt8, accel Nested(x Float32, y Float32, z Float32), topic String, received_at UInt32) ENGINE = MergeTree() ORDER BY (device_id, ts);'
```

---

## 7) Kafka → ClickHouse consumer (Python)

This service consumes from the `telemetry` Kafka topic, normalizes JSON, and inserts rows into ClickHouse.

Install dependencies (in a new/active venv):

```bash
pip install kafka-python clickhouse-connect or clickhouse-driver orjson
```

Save as `kafka_to_clickhouse.py`:

```python
# kafka_to_clickhouse.py
import os
import json
import time
from kafka import KafkaConsumer
from clickhouse_connect import get_client

KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP', 'localhost:9092')
TOPIC = os.getenv('KAFKA_TOPIC', 'telemetry')

consumer = KafkaConsumer(TOPIC,
                         bootstrap_servers=[KAFKA_BOOTSTRAP],
                         auto_offset_reset='earliest',
                         enable_auto_commit=True,
                         value_deserializer=lambda m: json.loads(m.decode('utf-8')))

ch = get_client(host='localhost', port=8123)

INSERT_SQL = 'INSERT INTO iot.telemetry (device_id, ts, lat, lon, battery, accel.x, accel.y, accel.z, topic, received_at) VALUES'

for msg in consumer:
    try:
        rec = msg.value
        payload = rec.get('payload', {})
        topic = rec.get('topic', '')
        # Flatten nested structure safely
        device_id = payload.get('device_id', 'unknown')
        ts = int(payload.get('ts', int(time.time())))
        lat = float(payload.get('lat', 0.0))
        lon = float(payload.get('lon', 0.0))
        battery = int(payload.get('battery', 0))
        accel = payload.get('accel', {})
        ax = float(accel.get('x', 0.0))
        ay = float(accel.get('y', 0.0))
        az = float(accel.get('z', 0.0))

        row = [device_id, ts, lat, lon, battery, ax, ay, az, topic, int(rec.get('received_at', int(time.time())))]
        ch.insert('iot.telemetry', [row])
        print('Inserted into ClickHouse:', row)
    except Exception as e:
        print('Error processing message:', e)
```

Run it:

```bash
export KAFKA_BOOTSTRAP=localhost:9092
python kafka_to_clickhouse.py
```

Notes:

* The ClickHouse client `clickhouse_connect` supports bulk inserts and typed inserts — adjust for higher throughput.
* For higher throughput, buffer rows and insert in batches.

---

## 8) Query the data in ClickHouse

Example queries (via HTTP API or clickhouse-client):

```sql
-- count per device
SELECT device_id, count() AS cnt
FROM iot.telemetry
GROUP BY device_id
ORDER BY cnt DESC
LIMIT 10;

-- latest location per device
SELECT device_id, anyLast(lat) AS lat, anyLast(lon) AS lon, anyLast(ts) AS ts
FROM iot.telemetry
GROUP BY device_id;

-- battery trend for a device
SELECT ts, battery
FROM iot.telemetry
WHERE device_id = 'phone-123'
ORDER BY ts DESC
LIMIT 100;
```

---

## 9) Hardening and production notes

* Authentication & TLS: enable Mosquitto authentication and TLS for mobile clients. Use secure Kafka clusters for production.
* Schema evolution: consider schema registry processes; store raw payloads in a raw table/column for reprocessing.
* Exactly-once / dedup: ClickHouse inserts are idempotent if you dedupe by unique keys or use dedup logic in consumers.
* Backpressure: if ClickHouse is slow, have your Kafka consumer buffer and expose metrics; consider Kafka Connect with bulk sink.
* Scaling: run multiple bridge instances with partitioning by device_id for high throughput.

---

## 10) Optional connector-based alternative

If you prefer managed connectors, replace the Python bridge / consumer with:

* **Kafka Connect MQTT Source connector** (Confluent or community plugin) → publishes MQTT → Kafka
* **ClickHouse Kafka engine or ClickHouse Sink Connector** (Altinity's connector) → consumes Kafka → ClickHouse

Connectors simplify operations but require installing/setting up Kafka Connect and connector plugins.

---

## 11) Short checklist to get everything working quickly

1. `git clone` or create project dir and add `docker-compose.yml` and `mosquitto/config/mosquitto.conf`.
2. `docker compose up -d` and confirm containers are healthy.
3. Start `mqtt_to_kafka.py` on the host pointing to `localhost`.
4. Start `kafka_to_clickhouse.py` on the host pointing to `localhost`.
5. Publish an MQTT message from phone app to `devices/phone-123/telemetry`.
6. Confirm message in Kafka with `kcat` or Kafdrop.
7. Confirm rows in ClickHouse with a query.

---

## 12) Next steps / enhancements

* Add TLS & username/password to Mosquitto + update phone client settings.
* Use buffered, batched inserts into ClickHouse to improve throughput.
* Add Prometheus metrics (expose counters from both Python services) and create Grafana dashboards.
* Add Debezium-style auditing or raw message archive in Parquet for historical reprocessing.

---
