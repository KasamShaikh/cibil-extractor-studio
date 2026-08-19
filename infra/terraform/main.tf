data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  suffix  = random_string.suffix.result
  base    = "${var.name_prefix}-${local.suffix}"
  use_aks = var.compute_target == "aks"
  use_aca = var.compute_target == "containerapps"
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
  count               = local.use_aks ? 1 : 0
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
  count                = local.use_aks ? 1 : 0
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks[0].kubelet_identity[0].object_id
}

# --- Azure Container Apps (compute_target = "containerapps") --- #
# Consumption environment: ingress is fronted by Microsoft-managed infrastructure,
# so no customer public IP is created (works on subscriptions that block public IPs).
resource "azurerm_container_app_environment" "aca" {
  count                      = local.use_aca ? 1 : 0
  name                       = "cae-${local.base}"
  location                   = var.location
  resource_group_name        = local.rg_name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
}

# Let the app's user-assigned identity pull the image from ACR (keyless).
resource "azurerm_role_assignment" "app_acr_pull" {
  count                = local.use_aca ? 1 : 0
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
