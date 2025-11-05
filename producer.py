import json
import os
import time
import atexit
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
WS_URL = "ws://localhost:8080/"
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
        future.result()
    except Exception as e:
        print(f"Publish failed: {e}", flush=True)

# Certstream message handler
def handle_cert_message(message):
    """Parse a Certstream message and push to Pub/Sub."""
    try:
        msg = json.loads(message)
        if msg.get("message_type") != "certificate_update":
            return

        all_domains = msg["data"]["leaf_cert"]["all_domains"]
        data = json.dumps({"urls": all_domains}).encode("utf-8")

        future = publisher.publish(topic_path, data)
        future.add_done_callback(on_publish_done)

        n = next(counter)
        if n % 50 == 0:
            print(f"Published {n} cert batches", flush=True)

        time.sleep(SLEEP_BETWEEN_BATCHES)

    except Exception as e:
        print(f"Error handling message: {e}", flush=True)



# Use Websocket to run Certstream
def run_websocket():
    def on_open(ws):
        print("Connection established to Certstream server.", flush=True)

        def ping_loop():
            while True:
                try:
                    ws.send("ping")
                    print("Sent ping", flush=True)
                except Exception as e:
                    print(f"Ping failed: {e}", flush=True)
                    break
                time.sleep(PING_INTERVAL)

        threading.Thread(target=ping_loop, daemon=True).start()

    def on_message(ws, message):
        handle_cert_message(message)

    def on_error(ws, error):
        print(f"[!] WebSocket error: {error}", flush=True)

    def on_close(ws, close_status_code, close_msg):
        print(f"[!] WebSocket closed ({close_status_code}): {close_msg}", flush=True)

    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=None, ping_timeout=None)
        except Exception as e:
            print(f"Connection failed: {e}. Retrying in 5s...", flush=True)
            time.sleep(5)

# Flush publisher on exit
@atexit.register
def flush_pubsub():
    print("Flushing Pub/Sub messages before exit...")
    publisher.stop()

# Start Certstream listener
if __name__ == "__main__":
    print(f"Starting Certstream producer (target: {TARGET_RATE} certs/sec)...", flush=True)
    run_websocket()
