terraform {
  backend "gcs" {
    bucket  = "gcptester101-tf-state"
    prefix  = "terraform/state"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable APIs
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "speech.googleapis.com",
    "translate.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com"
  ])
  service            = each.key
  disable_on_destroy = false
}

# Artifact Registry Repository
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "adk-demo-repo"
  description   = "Docker repository for adk demo services"
  format        = "DOCKER"
  depends_on    = [google_project_service.services]
}

# Cloud Storage Bucket
resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-adk-artifacts"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# Secrets
resource "google_secret_manager_secret" "bucket_name" {
  secret_id = "bucket-name-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "bucket_name_version" {
  secret      = google_secret_manager_secret.bucket_name.id
  secret_data = google_storage_bucket.artifacts.name
}

resource "google_secret_manager_secret" "gcs_artifacts_bucket" {
  secret_id = "gcs-artifacts-bucket"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "gcs_artifacts_bucket_version" {
  secret      = google_secret_manager_secret.gcs_artifacts_bucket.id
  secret_data = google_storage_bucket.artifacts.name
}

# Service Account for Cloud Run (Principle of Least Privilege)
resource "google_service_account" "cloud_run_sa" {
  account_id   = "video-translator-sa"
  display_name = "Service Account for Video Translator Cloud Run"
}

# IAM Bindings for Service Account
resource "google_storage_bucket_iam_member" "sa_storage" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "sa_secret_bucket" {
  secret_id = google_secret_manager_secret.bucket_name.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "sa_secret_gcs" {
  secret_id = google_secret_manager_secret.gcs_artifacts_bucket.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "sa_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "sa_speech" {
  project = var.project_id
  role    = "roles/speech.client"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "sa_translate" {
  project = var.project_id
  role    = "roles/cloudtranslate.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# Cloud Run Service (Initial generic container to bootstrap infrastructure)
# Cloud Build will subsequently update this with the real application image.
resource "google_cloud_run_v2_service" "default" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run_sa.email
    scaling {
      min_instance_count = 1
    }
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello" 
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      resources {
        limits = {
          memory = "8Gi"
          cpu    = "4"
        }
      }
    }
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret" "video_service_url" {
  secret_id = "video-service-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "video_service_url_version" {
  secret      = google_secret_manager_secret.video_service_url.id
  secret_data = google_cloud_run_v2_service.default.uri
}

resource "google_secret_manager_secret_iam_member" "sa_secret_video_service" {
  secret_id = google_secret_manager_secret.video_service_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# --- Additional Resources for Agent Engine Deployment ---

# Staging Bucket for ADK Agents
resource "google_storage_bucket" "staging" {
  name                        = "${var.project_id}-adk-staging"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# Service Account for ADK Agents (Vertex AI Reasoning Engine)
resource "google_service_account" "agent_sa" {
  account_id   = "adk-agent-sa"
  display_name = "Service Account for ADK Agents"
}

# IAM Bindings for Agent Service Account
resource "google_project_iam_member" "agent_sa_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_storage_bucket_iam_member" "agent_sa_staging" {
  bucket = google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_storage_bucket_iam_member" "agent_sa_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "agent_sa_secret_gcs" {
  secret_id = google_secret_manager_secret.gcs_artifacts_bucket.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "agent_sa_secret_video" {
  secret_id = google_secret_manager_secret.video_service_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Provide Service Usage Consumer role for agent SA to consume required APIs
resource "google_project_iam_member" "agent_sa_service_usage" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

