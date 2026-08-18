# The ONLY value a customer must set is subscription_id (see terraform.tfvars.example).
variable "subscription_id" {
  type        = string
  description = "Target Azure subscription id."
}

variable "location" {
  type        = string
  default     = "centralindia"
  description = "Region for AKS, ACR, Document Intelligence and Log Analytics."
}

variable "foundry_location" {
  type        = string
  default     = "southindia"
  description = "Region for the Azure OpenAI (Foundry) account. Must offer the chosen model (gpt-5.4-mini is not offered in centralindia)."
}

variable "name_prefix" {
  type        = string
  default     = "cibilx"
  description = "Short prefix for resource names. A random suffix is appended for global uniqueness."
}

variable "model_name" {
  type        = string
  default     = "gpt-5.4-mini"
  description = "Azure OpenAI model to deploy."
}

variable "model_version" {
  type        = string
  default     = "2026-03-17"
  description = "Model version."
}

variable "model_capacity" {
  type        = number
  default     = 50
  description = "Deployment capacity (thousands of TPM) for the GlobalStandard SKU."
}

variable "github_repo" {
  type        = string
  default     = "KasamShaikh/cibil-extractor-studio"
  description = "owner/name of the GitHub repo allowed to deploy via OIDC (federated credential subject)."
}

variable "k8s_namespace" {
  type    = string
  default = "cibil"
}

variable "k8s_service_account" {
  type    = string
  default = "cibil-extractor"
}

variable "aks_node_count" {
  type    = number
  default = 1
}

variable "aks_node_size" {
  type    = string
  default = "Standard_D2s_v3"
}
