# --- Azure AI: create new (create_ai = true) OR reference existing (create_ai = false) --- #

resource "azurerm_cognitive_account" "di" {
  count                 = var.create_ai ? 1 : 0
  name                  = "di-${local.base}"
  location              = var.location
  resource_group_name   = local.rg_name
  kind                  = "FormRecognizer"
  sku_name              = "S0"
  custom_subdomain_name = "di-${local.base}"
}

resource "azurerm_cognitive_account" "openai" {
  count                 = var.create_ai ? 1 : 0
  name                  = "oai-${local.base}"
  location              = var.foundry_location
  resource_group_name   = local.rg_name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.base}"
}

resource "azurerm_cognitive_deployment" "model" {
  count                = var.create_ai ? 1 : 0
  name                 = var.model_name
  cognitive_account_id = azurerm_cognitive_account.openai[0].id

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.model_capacity
  }
}

data "azurerm_cognitive_account" "di_existing" {
  count               = var.create_ai ? 0 : 1
  name                = var.existing_di_name
  resource_group_name = var.resource_group_name
}

data "azurerm_cognitive_account" "foundry_existing" {
  count               = var.create_ai ? 0 : 1
  name                = var.existing_foundry_name
  resource_group_name = var.resource_group_name
}

locals {
  di_id              = var.create_ai ? azurerm_cognitive_account.di[0].id : data.azurerm_cognitive_account.di_existing[0].id
  di_endpoint        = var.create_ai ? azurerm_cognitive_account.di[0].endpoint : data.azurerm_cognitive_account.di_existing[0].endpoint
  foundry_id         = var.create_ai ? azurerm_cognitive_account.openai[0].id : data.azurerm_cognitive_account.foundry_existing[0].id
  foundry_endpoint   = var.create_ai ? azurerm_cognitive_account.openai[0].endpoint : data.azurerm_cognitive_account.foundry_existing[0].endpoint
  foundry_deployment = var.create_ai ? azurerm_cognitive_deployment.model[0].name : var.foundry_deployment
}

# --- Workload identity for the app pod (keyless access to DI + OpenAI) --- #
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-app-${local.base}"
  location            = var.location
  resource_group_name = local.rg_name
}

resource "azurerm_federated_identity_credential" "app" {
  name      = "cibil-app"
  parent_id = azurerm_user_assigned_identity.app.id
  audience  = ["api://AzureADTokenExchange"]
  issuer    = azurerm_kubernetes_cluster.aks.oidc_issuer_url
  subject   = "system:serviceaccount:${var.k8s_namespace}:${var.k8s_service_account}"
}

resource "azurerm_role_assignment" "app_openai" {
  scope                = local.foundry_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "app_di" {
  scope                = local.di_id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
