output "resource_group" {
  value = azurerm_resource_group.rg.name
}

output "acr_name" {
  value = azurerm_container_registry.acr.name
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.aks.name
}

output "di_endpoint" {
  value = azurerm_cognitive_account.di.endpoint
}

output "foundry_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "foundry_deployment" {
  value = azurerm_cognitive_deployment.model.name
}

output "foundry_region" {
  value = var.foundry_location
}

# Client id used by the app pod (workload identity) — set as APP_CLIENT_ID.
output "app_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

# Values for GitHub Actions (OIDC login).
output "ci_client_id" {
  value = azurerm_user_assigned_identity.ci.client_id
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  value = var.subscription_id
}
