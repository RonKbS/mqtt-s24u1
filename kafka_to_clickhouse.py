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

        # External metadata (not from payload)
        device_id = rec.get('device_id', 'unknown')
        ts = int(rec.get('received_at', int(time.time())))

        # Battery payload fields
        present = bool(payload.get('present', False))
        status = str(payload.get('status', 'unknown'))
        health = str(payload.get('health', 'unknown'))

        level = int(payload.get('level', 0))
        scale = int(payload.get('scale', 100))
        battery_pct = float(payload.get('batteryPct', 0.0))

        plugged = str(payload.get('plugged', 'unknown'))
        voltage_mv = int(payload.get('m_voltage', 0))
        temperature_c = float(payload.get('c_temperature', 0.0))
        technology = str(payload.get('technology', 'unknown'))
        battery_state = str(payload.get('battery_state', 'null'))

        row = [
            device_id, ts, present, status, health, level, scale, battery_pct, plugged, voltage_mv, temperature_c, technology, battery_state, topic, int(time.time())
        ]

        ch.insert('iot.telemetry', [row])
        print('Inserted into ClickHouse:', row)

    except Exception as e:
        print('Error processing message:', e)