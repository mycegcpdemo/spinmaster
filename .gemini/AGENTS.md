# Skill: fde-compliance-upgrade

## Description
This skill automates the process of upgrading an existing repository to meet the strict FDE (Field Deployment Engineering) compliance standards. It systematically audits the codebase, ensures complete inline documentation, implements testing, CI/CD, security, Infrastructure as Code (IaC), and evaluates ADK Agents. 

**CRITICAL CONSTRAINTS:**
*   **Google Cloud Only:** You MUST use Google Cloud services and products for all operational aspects (e.g., CI/CD, Secret Management, Authentication, Hosting).
*   **IaC:** You MUST use Terraform for Infrastructure as Code.
*   **Repository:** The code is stored in GitLab. However, you MUST NOT use GitLab CI/CD. All pipelines must use Google Cloud Build.
*   **ADK Agents:** Any Agent Development Kit (ADK) agents found must have a holistic evaluation framework implemented according to official guidelines.

## Trigger
Use this skill when a user asks to "upgrade this repo to FDE standards", "run the FDE compliance check", or explicitly calls `/fde-compliance-upgrade`.

## Instructions

When this skill is activated, you must execute the following phases sequentially and autonomously. Do not stop until all phases are complete or you require user input to resolve a critical ambiguity or error.

### Phase 1: Automated Discovery & Exhaustive File-by-File Gap Analysis
1.  **Exhaustive Repository Scan:** Recursively list and examine every single folder and file in the repository. For *each individual file and directory*, evaluate if it meets the FDE standards or if it requires an upgrade:
    *   **Source Code Files (e.g., `.py`, `.ts`, `.go`):** Does this file contain core logic? If so, does a corresponding unit test file exist? Does this file contain hardcoded secrets, `.env` loading, or insecure credential management that needs to be migrated to Google Secret Manager?
    *   **ADK Agents:** Look for Python modules/packages defining an agent (e.g., directories containing an `__init__.py` that exposes an agent module with a `root_agent`). For any identified ADK agent:
        *   Are there corresponding evaluation datasets (`*.evalset.json`)?
        *   Is there an evaluation configuration (`test_config.json`) defining criteria?
        *   Are there programmatic test files (e.g., `pytest` files) that run `AgentEvaluator.evaluate()` or shell scripts invoking `adk eval`?
    *   **Directory Structure:** Does a directory representing a deployable service lack corresponding Terraform modules or configurations?
    *   **Security Configuration:** Are there any files indicating insecure authentication patterns? Are we missing configurations for Identity-Aware Proxy (IAP) where necessary?
2.  **Global Analysis:** Use `glob`, `grep_search`, and `codebase_investigator` to assess the repository as a whole against the remaining FDE requirements:
    *   **Evaluation/Testing:** What is the overall test coverage?
    *   **CI/CD (Google Cloud Build):** Is there a `cloudbuild.yaml` file at the root? Ensure there are NO `.gitlab-ci.yml` or GitHub Actions files.
    *   **Infrastructure (Terraform):** Are the overarching Terraform files (`main.tf`, `variables.tf`, `outputs.tf`) present to deploy the solution to Google Cloud?
3.  **Report Findings:** Compile a comprehensive list of all files, folders, agents, and global components that require upgrades. Briefly summarize this list to the user before proceeding to implementation.

### Phase 2: Holistic Code Comprehension & Inline Documentation
Before adding new features or infrastructure, you must ensure the existing codebase is fully understood and expertly documented internally.
1.  **System Comprehension:** Thoroughly read each file in the repository to understand its individual purpose and, crucially, how all the files work together to form the complete application architecture.
2.  **Comprehensive Commenting:** Systematically edit all files in the repository—especially source code files—to add professional, high-quality comments and docstrings.
    *   Add file-level docstrings explaining the file's role in the broader system.
    *   Add function, class, and method-level documentation detailing inputs, outputs, and side effects.
    *   Add inline comments to clarify complex logic or business rules. Ensure the comments explain *why* the code does what it does, contextualizing it within the overall system architecture you analyzed.

### Phase 3: Iterative Implementation
For each missing component or non-compliant file identified in Phase 1, iteratively implement the solution. **Crucially: Validate your changes at every step.**

1.  **ADK Agent Evaluation (Holistic Approach):**
    *   For every identified ADK agent lacking evaluations, you must establish a holistic evaluation suite.
    *   **Evaluation Criteria Config:** Generate a `test_config.json` file for the agent. This must include a holistic set of criteria to test trajectory, semantics, and safety. Recommended defaults: `tool_trajectory_avg_score` (e.g., 1.0), `response_match_score` (e.g., 0.8), `final_response_match_v2`, and `hallucinations_v1` (to ensure groundedness).
    *   **Evaluation Datasets:** If `*.evalset.json` files are missing, instruct the user to generate baseline scenarios using the `adk web` UI and save them to the repository, or generate a minimal valid schema programmatically if enough context is available.
    *   **Programmatic Integration:** Create a `pytest` file (e.g., `test_agent_eval.py`) that uses `google.adk.evaluation.agent_evaluator.AgentEvaluator.evaluate` to run the datasets against the agent module.
    *   **Validation:** Use `run_shell_command` to execute the evaluations (via `pytest` or `adk eval <AGENT_MODULE> <EVAL_SET> --config_file_path=<CONFIG>`).
2.  **Unit Testing:**
    *   For every regular source file identified as lacking tests, generate comprehensive unit tests using an appropriate framework for the language.
    *   Use `run_shell_command` to execute the tests locally. Fix any failures until the tests pass.
3.  **Security & Secret Management (Google Cloud):**
    *   Refactor every file identified as having hardcoded secrets or local environment variables to fetch credentials exclusively from **Google Secret Manager**. Ensure appropriate Google Cloud client libraries are added.
    *   If applicable, implement or configure Google Cloud Identity-Aware Proxy (IAP) for access control.
4.  **Infrastructure as Code (Terraform):**
    *   For directories needing deployment, and for the global infrastructure, generate Terraform configurations (`main.tf`, `variables.tf`, `outputs.tf`) to deploy the application exclusively to Google Cloud (e.g., Cloud Run, GKE, App Engine) with autoscaling enabled.
    *   **Workload-Specific Configuration:** If deploying to Cloud Run or App Engine, analyze the workload requirements (e.g., latency sensitivity, cold start intolerance, heavy compute/GPU needs). If the workload demands it, you MUST configure appropriate `min_instance` counts (minimum active instances), ensure adequate minimum memory allocations, and allocate minimum GPUs where required by the models.
    *   **Mandatory:** Ensure all Google Cloud IAM service accounts defined in the Terraform adhere strictly to the principle of least privilege.
    *   Run `terraform init` and `terraform validate` to ensure HCL syntax is correct.
5.  **CI/CD Pipeline (Google Cloud Build):**
    *   Generate a `cloudbuild.yaml` file to automate the pipeline. The pipeline should include steps for: running standard unit tests, running ADK evaluations, building artifacts, and deploying via Terraform.
    *   **CRITICAL:** Remove any `.gitlab-ci.yml` files if found. GitLab is strictly for source code hosting only.

### Phase 4: Comprehensive Production Documentation
After implementing the technical requirements, you must generate a highly detailed, production-quality `README.md` file. 

**CRITICAL RULES FOR README GENERATION:**
*   **Tone & Voice:** You MUST write strictly in the 3rd person (e.g., "The solution utilizes...", "This repository contains...").
*   **Language Framing:** DO NOT use phrasing that implies the code was "modified", "improved", or "upgraded to have...". Instead, state the features as inherent, present-tense facts (e.g., "The application features Google Cloud Build for CI/CD", not "Added Google Cloud Build").
*   **Completeness:** When reading the README, a user must be able to understand *everything* about the solution.

1.  **Generate `README.md`:** Create or overwrite the `README.md` file to include the following sections at a minimum:
    *   **Solution Overview:** A comprehensive explanation of the project's purpose and functionality.
    *   **Architecture Diagram:** Generate a Mermaid.js diagram representing the complete infrastructure, data flow, and networking on Google Cloud.
    *   **Core System Features:** Explicitly detail how the following baseline capabilities are present within the solution:
        *   **Evaluation:** Explain the ADK evaluation framework and/or other evaluation methodologies utilized.
        *   **CI/CD:** Detail the automated deployment pipeline using Google Cloud Build.
        *   **Security:** Detail the security posture, including the usage of Google Secret Manager, Least Privilege IAM, and OAuth 2.0 authentication flows utilized by the application.
        *   **Unit Testing:** Describe the comprehensive unit testing framework covering the entire codebase.
        *   **Autoscaling & Workload Configuration:** Document how the cloud infrastructure (managed via Terraform) inherently scales based on demand. You MUST explicitly call out specific configurations made for Cloud Run or App Engine workloads, such as minimum active instances (`min_instances`) configured to prevent cold starts, minimum memory allocations, and any GPU allocations required by the models.
        *   **Terraform:** Explain the use of Infrastructure as Code for predictable, reproducible environments.
    *   **Deployment & Operations:** Step-by-step instructions on how to run tests locally, deploy the solution, manage secrets, and operate the CI/CD pipeline.
    *   **Generative AI & Models:** A dedicated section detailing the specific generative AI features of the solution. You must explicitly list any models used (including video, audio, image, text-to-speech, and speech-to-text models) and describe exactly how they were implemented. If the repository contains a GenMedia solution, you must explain what the solution does and articulate its specific benefits to the media and entertainment industry.
    *   **FDE Competency Implementations:** Create a dedicated section explaining *exactly how* the solution implements the required FDE competencies. You must cite specific repository features, architectural patterns, and code components as evidence for the following categories:
        *   **AI/ML Engineering:** (e.g., Agentic & Multi-Agent Systems, RAG, Model Selection & Tuning, LLM Ops and Evaluation, Domain-Applied AI/ML Expertise).
        *   **Scoping and Documentation:** (e.g., Problem Definition, Technical Scope, Stakeholder Alignment, System Design Artifacts, Decision Records, API/Operational Documentation).
        *   **Security, Privacy, and Compliance:** (e.g., Authentication & Authorization, Infrastructure & Network Security, Data Protection, AI-Specific Security, Compliance & Governance).
        *   **Reliability & Resilience:** (e.g., Availability Design, Observability, Failure & Recovery Testing, Graceful Degradation).
        *   **Performance & Cost Optimization:** (e.g., Scalability & Elasticity, Resource Efficiency, AI Cost Management).
        *   **Operational Excellence:** (e.g., CI/CD & Deployment, Infrastructure as Code, AI Lifecycle Management, Testing & Quality Engineering).
        *   **Designing for Change:** (e.g., Modularity & Abstraction, Configuration Management, API Design & Versioning, Extensibility).

## Constraints & Rules
*   **Validation is Key:** Do not leave broken code or failing evaluations. If you write a test or eval configuration, run it. If it fails, fix the code, test, or config.
*   **Security First:** Never generate code that logs, prints, or exposes credentials. All secrets MUST use Google Secret Manager.