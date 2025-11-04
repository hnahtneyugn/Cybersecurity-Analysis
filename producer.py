import json
import os
import time
import atexit
import certstream
import threading
import websocket
from itertools import count
from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.types import (
    LimitExceededBehavior,
    PublisherOptions,
    PublishFlowControl,
)

# GCP Project details
PROJECT_ID = os.environ.get("PROJECT_ID", "hip-host-475008-d5")
TOPIC_ID = "urlstream"
PING_INTERVAL = 30 

counter = count()

# Batch and flow control
batch_settings = pubsub_v1.types.BatchSettings(
    max_bytes=256 * 1024,
    max_latency=0.1,
    max_messages=10,
)

flow_control_settings = PublishFlowControl(
    message_limit=1000,
    byte_limit=5 * 1024 * 1024,
    limit_exceeded_behavior=LimitExceededBehavior.BLOCK,
)

publisher = pubsub_v1.PublisherClient(
    batch_settings=batch_settings,
    publisher_options=PublisherOptions(flow_control=flow_control_settings)
)

topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

TARGET_RATE = 50
SLEEP_BETWEEN_BATCHES = 1.0 / TARGET_RATE

# Callback for async publish
def on_publish_done(future):
    try:
        message_id = future.result()
    except Exception as e:
        print(f"Publish failed: {e}", flush=True)

# Certstream message handler
def on_message(message, _):
    if message.get("message_type") != "certificate_update":
        return
    try:
        all_domains = message["data"]["leaf_cert"]["all_domains"]
        data = json.dumps({"urls": all_domains}).encode("utf-8")

        future = publisher.publish(topic_path, data)
        future.add_done_callback(on_publish_done)

        n = next(counter)
        if n % 50 == 0:
            print(f"Published {n} cert batches", flush=True)

        time.sleep(SLEEP_BETWEEN_BATCHES)  # maintain target rate

    except Exception as e:
        print(f"Error in on_message: {e}", flush=True)


# Ping the server every 30s
def start_pinger(ws_url):
    def ping_loop():
        while True:
            try:
                ws = websocket.create_connection(ws_url)
                ws.ping()
                ws.close()
            except Exception:
                pass
            time.sleep(PING_INTERVAL)
    threading.Thread(target=ping_loop, daemon=True).start()

# Flush publisher on exit
@atexit.register
def flush_pubsub():
    print("Flushing Pub/Sub messages before exit...")
    publisher.stop()

# Start Certstream listener
print(f"Starting Certstream producer (target: {TARGET_RATE} certs/sec)...", flush=True)
ws_url = "ws://localhost:8080/"
start_pinger(ws_url)
certstream.listen_for_events(message_callback=on_message, url=ws_url)
