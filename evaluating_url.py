import base64
import json
import re
import tensorflow as tf
import numpy as np
from google.cloud import bigquery
import os
import datetime
import sys

PROJECT_ID = "hip-host-475008-d5"
DATASET_ID = "big_data_uet_dataset"
TABLE_ID = "classified_urls"
THRESHOLD = 0.5
BATCH_SIZE = 500

# Lazy-loaded global variables
model = None
bq_client = None
table_ref = None


def log(msg):
    """Simple prefixed logger with flush for Cloud Run."""
    print(f"[LOG] {datetime.datetime.now().isoformat()} - {msg}", flush=True)


def get_model():
    """Lazy-loads model once per container."""
    global model
    if model is None:
        log("Loading TensorFlow model...")
        try:
            model = tf.keras.models.load_model("url_classifier_model.keras")
            log("Model loaded successfully.")
        except Exception as e:
            log(f"ERROR loading model: {e}")
            model = None
    return model


def get_bigquery_client():
    """Lazy-loads BigQuery client."""
    global bq_client, table_ref
    if bq_client is None:
        log("Initializing BigQuery client...")
        try:
            bq_client = bigquery.Client(project=PROJECT_ID)
            table_ref = bq_client.dataset(DATASET_ID).table(TABLE_ID)
            log("BigQuery client initialized.")
        except Exception as e:
            log(f"ERROR initializing BigQuery client: {e}")
            bq_client = None
    return bq_client


def create_batches(data, batch_size):
    for i in range(0, len(data), batch_size):
        yield data[i:i + batch_size]


def preprocess_url(url):
    url = url.lower()
    url = re.sub(r'https?://|www\.', '', url)
    url = url[:500]
    url = " ".join(list(url))
    return url


def process_pubsub(event, _):
    """Triggered from a Pub/Sub message."""
    log("=== Function triggered ===")

    try:
        log("Decoding Pub/Sub message...")
        pubsub_message = base64.b64decode(event["data"]).decode("utf-8")
        data = json.loads(pubsub_message)
        url_list = data.get("urls", [])
        log(f"Received {len(url_list)} URLs.")

        if not url_list:
            log("No URLs found in message, exiting.")
            return

        # Filter wildcards
        filtered_urls = [u for u in url_list if not u.startswith("*.")]
        log(f"{len(filtered_urls)} URLs after filtering wildcards.")

        model = get_model()
        if model is None:
            log("Model not available, aborting.")
            return

        bq_client = get_bigquery_client()
        if bq_client is None:
            log("BigQuery client not available, aborting.")
            return

        rows_to_insert = []

        for url_batch in create_batches(filtered_urls, BATCH_SIZE):
            log(f"Processing batch of {len(url_batch)} URLs...")

            processed_batch = [preprocess_url(url) for url in url_batch]
            processed_batch = np.array(processed_batch, dtype=object)

            log("Running model prediction...")
            predictions = model.predict(processed_batch, verbose=0)
            log("Model prediction complete.")

            for url, prediction in zip(url_batch, predictions):
                score = float(prediction[0])
                label = "MALICIOUS" if score > THRESHOLD else "BENIGN"
                rows_to_insert.append({
                    "url": url,
                    "score": round(score, 2),
                    "classification": label,
                    "time_added": datetime.datetime.now(datetime.UTC).isoformat()
                })

        if rows_to_insert:
            log(f"Inserting {len(rows_to_insert)} rows into BigQuery...")
            errors = bq_client.insert_rows_json(table_ref, rows_to_insert)
            if errors:
                log(f"BigQuery insert errors: {errors}")
            else:
                log("Inserted successfully.")
        else:
            log("No rows to insert.")

        log("=== Function completed successfully ===")

    except Exception as e:
        log(f"ERROR during processing: {e}")
