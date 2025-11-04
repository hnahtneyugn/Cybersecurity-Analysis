import json
import os
import time
import atexit
import requests
from collections import deque
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

counter = count()
total_domains = 0

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

TARGET_RATE = 10
SLEEP_BETWEEN_BATCHES = 1.0 / TARGET_RATE

STATE_FILE = "crt_state.json"
RECENT_CACHE_SIZE = 20000

# Create a cache of 20k most recently crawled URL to prevent duplicates
recent_domains = deque(maxlen=RECENT_CACHE_SIZE)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("max_cert_id")
        except Exception:
            pass
    return None


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"max_cert_id": max_cert_id}, f)
    except Exception as e:
        print(f"Failed to save state: {e}", flush=True)


def is_valid_domain(d):
    if not d or d.startswith("*."):
        return False
    
    # Filter out certificate metadata (contains spaces, special phrases)
    if any(phrase in d.lower() for phrase in [
        "terms of use", "see www.", "(c)", "http://", "https://",
        "go to", "repository", "resources/cps", "incits", 
        "copyright", "visit", "refer to"]):
        return False
    
    # Must contain at least one dot (domain.tld)
    if "." not in d:
        return False
    
    # Should not have spaces
    if " " in d:
        return False
    
    # Otherwise, return True
    return True


def on_publish_done(future):
    try:
        message_id = future.result()
    except Exception as e:
        print(f"Publish failed: {e}", flush=True)


@atexit.register
def flush_pubsub():
    print("Flushing Pub/Sub messages before exit...", flush=True)
    publisher.stop()


print(f"Starting crt.sh certificate producer...", flush=True)

try:
    test_future = publisher.publish(topic_path, b"test message")
    print(f"Test publish successful: {test_future.result()}", flush=True)
except Exception as e:
    print(f"Test publish failed: {e}", flush=True)
    exit(1)


# Track the highest certificate ID we've seen
max_cert_id = load_state()
if max_cert_id:
    print(f"Resuming from saved max_cert_id={max_cert_id}", flush=True)
else:
    print("Starting fresh (no saved state)", flush=True)

print("Starting certificate stream from crt.sh...", flush=True)

# Common TLDs to monitor for new certificates
tlds = ['.com', '.net', '.org', '.io', '.dev', '.app', '.co', '.ai', '.xyz']
tld_index = 0
backoff = 10
reset_counter = 0

while True:
    try:
        # Rotate through TLDs to get diverse certificate data
        search_term = f"%.{tlds[tld_index]}"
        tld_index = (tld_index + 1) % len(tlds)
        reset_counter += 1

        # Periodically reset max_cert_id to avoid stalls
        if reset_counter % 1000 == 0:
            print("Resetting max_cert_id to avoid stalls", flush=True)
            max_cert_id = None
            save_state()
        
        print(f"Querying for {search_term}...", flush=True)
        
        # Use the identity search which is more reliable
        params = {
            "Identity": search_term,
            "output": "json"
        }
        
        if max_cert_id:
            params["minCertID"] = max_cert_id + 1
        
        response = requests.get(
            url="https://crt.sh/",
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            },
            timeout=30
        )
        
        print(f"Response status: {response.status_code}, length: {len(response.text)}", flush=True)
        
        if response.status_code == 200:
            # Check if response is HTML (error page) or JSON
            content_type = response.headers.get('content-type', '')
            
            if not response.text.strip().startswith('['):
                print("Not a JSON array, skipping...", flush=True)
                time.sleep(10)
                continue
            
            try:
                # Try to parse JSON
                text = response.text.strip()
                
                # Log first 200 chars for debugging
                print(f"Response preview: {text[:200]}", flush=True)
                
                if not text or text == '[]':
                    empty_counter += 1
                    wait = min(60 * empty_counter, 300)  # up to 5 min
                    print(f"No new certs, sleeping {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                else:
                    empty_counter = 0
                
                certs = json.loads(text)
                
                if not isinstance(certs, list):
                    print(f"Unexpected response format: {type(certs)}", flush=True)
                    time.sleep(10)
                    continue
                
                print(f"Found {len(certs)} certificates", flush=True)
                
                for cert in certs:
                    cert_id = cert.get("id")
                    
                    # Update max_cert_id
                    if cert_id and (max_cert_id is None or cert_id > max_cert_id):
                        max_cert_id = cert_id
                    
                    # Extract domain names
                    domains = []
                    name_value = cert.get("name_value", "")
                    common_name = cert.get("common_name", "")
                    
                    if name_value:
                        domains.extend([d.strip() for d in name_value.split("\n") if d.strip()])
                    if common_name and common_name not in domains:
                        domains.append(common_name)
                    
                    # Remove duplicates and filter invalid domains
                    domains = [d for d in domains if is_valid_domain(d) and d not in recent_domains]
                    recent_domains.extend(domains)
                    
                    if domains:
                        n = next(counter)
                        total_domains += len(domains)
                        
                        payload = json.dumps({"urls": domains}).encode("utf-8")
                        future = publisher.publish(topic_path, payload)
                        future.add_done_callback(on_publish_done)
                        
                        if n % 50 == 0:
                            print(f"Published {n} batches | {total_domains} domains | Latest: {domains[0][:50]}", flush=True)
                        
                        time.sleep(SLEEP_BETWEEN_BATCHES)
                
                # Small delay before next query
                time.sleep(2)
                
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}", flush=True)
                print(f"Response text (first 500 chars): {response.text[:500]}", flush=True)
                time.sleep(10)
                
        elif response.status_code == 429:
            print(f"Rate limited, waiting 60s...", flush=True)
            time.sleep(60)
        elif response.status_code == 404:
            print(f"No results found, trying next TLD...", flush=True)
            time.sleep(5)
        else:
            print(f"HTTP {response.status_code}, retrying in 10s...", flush=True)
            time.sleep(10)
            
    except requests.RequestException as e:
        print(f"Request error: {e}, retrying in 10s...", flush=True)
        time.sleep(10)
    except KeyboardInterrupt:
        print("\nShutting down gracefully...", flush=True)
        break
    except Exception as e:
        print(f"Unexpected error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        time.sleep(10)

print("Shutdown complete", flush=True)