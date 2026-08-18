# CIBIL Extractor Studio

A proof-of-concept that turns a single CIBIL Credit
Information Report (PDF) into the **Full Detail Excel workbook**, with a
**Maker–Checker** review step in between. It implements the standing rules from
*CIBIL Extractor Master Commands & Rules*.

> Proof of concept. Runs entirely on your machine — no data leaves the device.
> Use it with the provided **synthetic** test report.

---

## Quick start

```powershell
# from this folder
./run.ps1
```

Then open <http://127.0.0.1:8010>.

`run.ps1` creates a virtual environment, installs dependencies and starts the
server. To run manually:

```powershell
python -m venv .venv
./.venv/Scripts/Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.app:app --port 8010
```

---

## Workflow

1. **Upload** a single CIBIL report PDF and click *Extract*.
2. **Maker Review** — extracted Customer Details, the sequenced Account Summary,
   reconciliation checks and Enquiries are shown. Low-confidence cells are
   highlighted. Edit anything, *Save*, then *Submit to Checker*.
3. **Checker** — an independent review of the data and reconciliation.
   *Approve* generates the Excel, or *Reject* returns it to the maker with a reason.
4. **Download** the generated Full Detail workbook. An audit trail is recorded.

---

## What the rules engine does

- **Account Summary** columns A–K exactly as the template.
- **Column H (DPD Last 12 Months)** — latest-to-oldest, pipe-separated; any
  positive DPD token is shown in **red rich text** (only the value, not the cell).
  `STD`/`000` → 0, `XXX`/dash → blank.
- **Sequencing** — positive-DPD accounts first, then remaining accounts by
  *Date Opened* (newest first).
- **Overdue Amount** highlighted red when greater than zero.
- **Column K** — NPA at 90+ DPD, SMA bands, Suit Filed / Written Off where present.
- **Ownership Summary** and **Loan Type Summary** recomputed with **Total** rows;
  Loan Type Summary includes total and closed-account counts (outstanding = 0).
- **Enquiries** — summary table plus a **purpose-wise** summary.
- **Reconciliation** — account count, total sanction / outstanding / overdue,
  closed accounts and enquiry counts are checked against the report's own totals.
- **Validation** — Financier Name cannot contain label words (Current, Balance, …).

The Ownership and Loan Type summaries are **recomputed** from the account rows
(single source of truth); the report's printed totals are used only to
reconcile.

---

## Project layout

```
backend/
  app.py        FastAPI app, maker-checker routes, in-memory job store
  parser.py     PDF -> structured data (pypdfium2) + field validation
  rules.py      DPD, sequencing, summaries, reconciliation, status derivation
  excelgen.py   Fills the 5-sheet workbook (openpyxl) with formatting
frontend/
  index.html    Single-page maker-checker UI
  styles.css
  app.js
requirements.txt
run.ps1
```

---

## How this maps to production

In production the same pipeline runs behind an async, queue-based platform
(**AKS + KEDA + Service Bus**): the extraction combines Azure Document
Intelligence (OCR / layout) with a **Microsoft Foundry** model (gpt-5.4-mini)
applying these rules, and the Maker–Checker workflow, multi-tenant access and
audit trail are backed by **Azure SQL** (metadata only; artifacts in Blob) — all
keyless (managed identity + RBAC).

---

## Deploy to Azure (AKS)

Everything needed to deploy is in this repo:

- `infra/terraform/` — provisions AKS, ACR, Document Intelligence, Azure OpenAI
  (gpt-5.4-mini), Log Analytics, and two keyless identities (workload identity
  for the app; a GitHub-federated identity for CI).
- `Dockerfile` + `deploy/k8s/` — the container and Kubernetes manifests.
- `.github/workflows/deploy.yml` — GitHub Actions builds the image in ACR and
  deploys to AKS via OIDC. **No secrets or IDs are committed to the repo.**

### 1. Provision the infrastructure

The only value you must supply is your **subscription id**.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # edit: subscription_id = "<your-sub-id>"
terraform init
terraform apply
```

> `gpt-5.4-mini` is deployed in `southindia` (it is not offered in
> `centralindia`); AKS / ACR / Document Intelligence use `centralindia`. Override
> `location`, `foundry_location`, or `model_name` in `terraform.tfvars` as needed.

### 2. Wire the repo to Azure (from Terraform outputs)

```bash
gh secret set AZURE_CLIENT_ID       -b "$(terraform output -raw ci_client_id)"
gh secret set AZURE_TENANT_ID       -b "$(terraform output -raw tenant_id)"
gh secret set AZURE_SUBSCRIPTION_ID -b "$(terraform output -raw subscription_id)"
gh secret set APP_CLIENT_ID         -b "$(terraform output -raw app_client_id)"

gh variable set ACR_NAME            -b "$(terraform output -raw acr_name)"
gh variable set ACR_LOGIN_SERVER    -b "$(terraform output -raw acr_login_server)"
gh variable set RESOURCE_GROUP      -b "$(terraform output -raw resource_group)"
gh variable set AKS_NAME            -b "$(terraform output -raw aks_name)"
gh variable set DI_ENDPOINT         -b "$(terraform output -raw di_endpoint)"
gh variable set FOUNDRY_ENDPOINT    -b "$(terraform output -raw foundry_endpoint)"
gh variable set FOUNDRY_DEPLOYMENT  -b "$(terraform output -raw foundry_deployment)"
gh variable set FOUNDRY_API_VERSION -b "2025-04-01-preview"
gh variable set FOUNDRY_REGION      -b "$(terraform output -raw foundry_region)"
```

### 3. Deploy

Push to `main` (or run the workflow manually). GitHub Actions builds the image
and deploys it, then prints the public URL of the `LoadBalancer` service.

```bash
git push origin main
# or: gh workflow run "Build and deploy to AKS"
```

Terraform outputs feed GitHub **secrets** (identity / subscription / tenant) and
**variables** (resource names / endpoints); the container authenticates to Azure
with **workload identity** — keyless, no stored secrets.

### Local development

Copy `.env.example` to `.env`, fill in your `DI_ENDPOINT` and `FOUNDRY_*`
endpoints (keyless — be `az login`'d with access), then run `./run.ps1`.
