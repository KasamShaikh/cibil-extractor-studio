data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  suffix = random_string.suffix.result
  base   = "${var.name_prefix}-${local.suffix}"
}

# Resource group: create a new one, or use an existing one (var.resource_group_name).
resource "azurerm_resource_group" "rg" {
  count    = var.resource_group_name == "" ? 1 : 0
  name     = "rg-${local.base}"
  location = var.location
}

data "azurerm_resource_group" "existing" {
  count = var.resource_group_name == "" ? 0 : 1
  name  = var.resource_group_name
}

locals {
  rg_name = var.resource_group_name == "" ? azurerm_resource_group.rg[0].name : data.azurerm_resource_group.existing[0].name
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "log-${local.base}"
  location            = var.location
  resource_group_name = local.rg_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_registry" "acr" {
  name                = "acr${var.name_prefix}${local.suffix}"
  resource_group_name = local.rg_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_kubernetes_cluster" "aks" {
  name                = "aks-${local.base}"
  location            = var.location
  resource_group_name = local.rg_name
  dns_prefix          = "aks${var.name_prefix}${local.suffix}"

  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name       = "system"
    node_count = var.aks_node_count
    vm_size    = var.aks_node_size
  }

  identity {
    type = "SystemAssigned"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
  }

  timeouts {
    create = "45m"
    update = "45m"
  }
}

# Let AKS pull images from ACR without credentials.
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}
