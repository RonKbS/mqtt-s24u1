
# to run on phone

import sounddevice as sd
import numpy as np
import time
import json
import paho.mqtt.client as mqtt

BROKER = "192.168.1.100.3"  # your laptop IP
TOPIC = "devices/phone-123/audio"

client = mqtt.Client()
client.connect(BROKER, 1883, 60)

def audio_callback(indata, frames, time_info, status):
    rms = np.sqrt(np.mean(indata**2))
    peak = np.max(np.abs(indata))
    db = 20 * np.log10(rms + 1e-9)

    payload = {
        "device_id": "phone-123",
        "ts": int(time.time()),
        "rms": float(rms),
        "peak": float(peak),
        "db": float(db)
    }

    client.publish(TOPIC, json.dumps(payload))

with sd.InputStream(callback=audio_callback, channels=1, samplerate=16000, blocksize=16000):
    while True:
        time.sleep(1)
