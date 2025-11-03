# Google Cloud Storage (GCS) for training data and scripts
resource "google_storage_bucket" "terraform_big_data_bucket" {
  name          = var.gcs_bucket_name
  location      = var.location
  force_destroy = true


  lifecycle_rule {
    condition {
      age = 1
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}


# GCS object to store code and model
resource "google_storage_bucket_object" "object" {
  name   = var.gcs_object_name
  bucket = google_storage_bucket.terraform_big_data_bucket.name
  source = "../code_and_model.zip"
}

resource "google_compute_firewall" "vm_egress" {
  name    = "${var.vm_name}-allow-egress"
  network = "default" # Make sure this is the network your VM is on

  # Allow all outbound traffic from your VM
  allow {
    protocol = "all"
  }
  
  direction     = "EGRESS"
  destination_ranges = ["0.0.0.0/0"] # This means "anywhere on the internet"
  
  # This targets your VM. 
  # We'll add a "tag" to the VM to link it.
  target_tags = ["producer-vm"]
}

# Compute Instance VM that acts as a Producer to Pub/Sub
resource "google_compute_instance" "terraform_vm" {
  name = var.vm_name
  machine_type = "e2-micro"
  zone = "us-west1-b"
  tags = ["producer-vm"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }

  service_account {
    email  = "938296883293-compute@developer.gserviceaccount.com"
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e

    # Update and install system dependencies
    apt-get update -y
    apt-get install -y python3-pip python3-venv ca-certificates wget

    # Create Python virtual environment
    python3 -m venv /home/certstream_env

    # Activate venv and upgrade pip
    source /home/certstream_env/bin/activate
    pip install --upgrade pip

    # Install required Python packages inside venv
    pip install certstream websocket-client google-cloud-pubsub

    # Copy the producer script from GCS
    gsutil cp gs://${var.gcs_bucket_name}/producer.py /home/producer.py

    # Set PROJECT_ID environment variable
    export PROJECT_ID="${var.project}"

    # Run the producer in background using venv Python
    nohup /home/certstream_env/bin/python -u /home/producer.py > /home/producer.log 2>&1 &
  EOT
}



# Pub/Sub topic for realtime streaming testing data from Certstream
resource "google_pubsub_topic" "terraform_pubsub_topic" {
  name = var.pubsub_topic_name
}


# Cloud Run Functions for evaluating URLs
resource "google_cloudfunctions2_function" "terraform_function" {
  name = var.cloudrun_name
  location = var.region
  description = "Cloud Run Function for evaluating URLs on a pre-trained model"

  build_config {
    runtime = "python311"
    entry_point = "process_pubsub"
    environment_variables = {
      GOOGLE_FUNCTION_SOURCE = "evaluating_url.py"
    }
    source {
      storage_source {
        bucket = google_storage_bucket.terraform_big_data_bucket.name
        object = google_storage_bucket_object.object.name
      }
    }
  }
  
  service_config {
    available_memory = "4G"
    timeout_seconds  = 300
    environment_variables = {
      PROJECT_ID = var.project
    }
  }

  event_trigger {
    event_type = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic = google_pubsub_topic.terraform_pubsub_topic.id
    retry_policy = "RETRY_POLICY_RETRY"
  }
}

# BigQuery Dataset
resource "google_bigquery_dataset" "terraform_big_data_dataset" {
  dataset_id = var.bq_dataset_name
  location   = var.region
} 


# BigQuery table to store classified URLs
resource "google_bigquery_table" "terraform_big_data_table" {
  dataset_id = google_bigquery_dataset.terraform_big_data_dataset.dataset_id
  table_id   = var.bq_table_name
  schema     = file(var.bq_table_schema)

  time_partitioning {
    type = "DAY"
    field = "time_added"
  }

  clustering = ["classification"]
}