output "resource_group" {
  value = local.rg_name
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
  value = local.di_endpoint
}

output "foundry_endpoint" {
  value = local.foundry_endpoint
}

output "foundry_deployment" {
  value = local.foundry_deployment
}

output "foundry_region" {
  value = var.foundry_location
}

output "app_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "ci_client_id" {
  value = azurerm_user_assigned_identity.ci.client_id
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  value = var.subscription_id
}
