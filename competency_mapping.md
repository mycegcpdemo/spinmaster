# FDE Competency Mapping

| Competency | Status | Evidence Example (Code Snippet) |
| :--- | :--- | :--- |
| **Infrastructure as Code (IaC)** | Met | Used Terraform to deploy to Google Cloud with least-privilege IAM. <br><br>**Snippet from `main.tf`:**<br>```hcl<br>resource "google_cloud_run_v2_service" "default" {<br>  name     = var.service_name<br>  location = var.region<br>``` |
| **CI/CD Pipelines** | Met | Created Google Cloud Build pipeline (`cloudbuild.yaml`) handling testing, build, and deployment. <br><br>**Snippet from `cloudbuild.yaml`:**<br>```yaml<br>  - name: 'hashicorp/terraform:1.5.0'<br>    args: ['apply', '-auto-approve', '-var=project_id=${PROJECT_ID}', '-var=region=${_REGION}']<br>``` |
| **Agent Evaluation** | Met | Established holistic ADK evaluation using `evalset.json`, `test_config.json`, and `pytest`. <br><br>**Snippet from `image_Agent/test_config.json`:**<br>```json<br>  "criteria": {<br>    "tool_trajectory_avg_score": 1.0,<br>    "response_match_score": 0.8<br>``` |
| **Security & Credential Management** | Met | Refactored environment variables to fetch from Google Secret Manager. Uses Service Accounts with least privilege. <br><br>**Snippet from `video-translator-service/main.py`:**<br>```python<br>def get_secret(secret_id: str, project_id: str, version_id: str = "latest") -> str:<br>    client = secretmanager.SecretManagerServiceClient()<br>``` |
| **Unit Testing** | Met | Added comprehensive tests using pytest for core services. <br><br>**Snippet from `video-translator-service/test_main.py`:**<br>```python<br>@patch("main.process_translation_workflow")<br>def test_translate_raw(mock_download, mock_upload, mock_process, tmp_path):<br>``` |
| **Identity-Aware Proxy (IAP)** | Not Applicable | The public-facing ad generation API demo did not require strict user authentication at the edge for this iteration. To achieve this, IAP can be enabled via Terraform on the load balancer sitting in front of Cloud Run. |
