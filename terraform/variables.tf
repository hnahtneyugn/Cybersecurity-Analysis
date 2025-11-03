variable "credentials" {
  description = "My Credentials"
  type        = string
}

variable "project" {
  description = "My Main Project"
  type        = string
}

variable "region" {
  description = "Project Region"
  type        = string
}

variable "location" {
  description = "Project Location"
  type        = string
}

variable "gcs_bucket_name" {
  description = "My Storage Bucket Name"
  type        = string
}

variable "gcs_object_name" {
  description = "GCS Object Name"
  type = string
}

variable "vm_name" {
  description = "VM instance Name" 
  type = string
}

variable "pubsub_topic_name" {
  description = "PubSub Topic Name"
  type = string
}

variable "cloudrun_name" {
  description = "Cloud Run Function Name"
  type = string
}

variable "bq_dataset_name" {
  description = "My BigQuery Dataset Name"
  type        = string
}

variable "bq_table_name" {
  description = "My BigQuery Table Name"
  type = string
}

variable "bq_table_schema" {
  description = "Schema for BigQuery table"
  type = string
}