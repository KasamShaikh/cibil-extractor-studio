variable "subscription_id" {
  type        = string
  description = "Target Azure subscription id."
}

variable "location" {
  type        = string
  default     = "centralindia"
  description = "Region for AKS, ACR, Log Analytics (and Document Intelligence when created)."
}

variable "compute_target" {
  type        = string
  default     = "aks"
  description = "Where the app runs: 'aks' (default, for customer environments) or 'containerapps' (managed ingress, no public IP required)."
  validation {
    condition     = contains(["aks", "containerapps"], var.compute_target)
    error_message = "compute_target must be either 'aks' or 'containerapps'."
  }
}

variable "resource_group_name" {
  type        = string
  default     = ""
  description = "Existing resource group to deploy into. Leave empty to create a new one."
}

variable "create_ai" {
  type        = bool
  default     = true
  description = "Provision new Document Intelligence + Azure OpenAI + model (true), or reuse existing ones (false)."
}

variable "existing_di_name" {
  type        = string
  default     = ""
  description = "Existing Document Intelligence account name (used when create_ai = false)."
}

variable "existing_foundry_name" {
  type        = string
  default     = ""
  description = "Existing Azure OpenAI / Foundry account name (used when create_ai = false)."
}

variable "foundry_deployment" {
  type        = string
  default     = "gpt-5.4-mini"
  description = "Model deployment name the app calls (created when create_ai = true; must already exist when false)."
}

variable "foundry_location" {
  type        = string
  default     = "southindia"
  description = "Region for the Azure OpenAI account when created. Must offer the model (gpt-5.4-mini is not in centralindia)."
}

variable "name_prefix" {
  type    = string
  default = "cibilx"
}

variable "model_name" {
  type    = string
  default = "gpt-5.4-mini"
}

variable "model_version" {
  type    = string
  default = "2026-03-17"
}

variable "model_capacity" {
  type    = number
  default = 50
}

variable "github_repo" {
  type    = string
  default = "KasamShaikh/cibil-extractor-studio"
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
