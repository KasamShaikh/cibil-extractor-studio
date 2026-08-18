# CI/CD identity for GitHub Actions — a user-assigned identity with a GitHub
# federated credential (OIDC). No client secret, and no app-registration /
# directory permissions required.

resource "azurerm_user_assigned_identity" "ci" {
  name                = "id-ci-${local.base}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_federated_identity_credential" "ci_main" {
  name                = "github-main"
  resource_group_name = azurerm_resource_group.rg.name
  parent_id           = azurerm_user_assigned_identity.ci.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = "repo:${var.github_repo}:ref:refs/heads/main"
}

# Build + push images to ACR (az acr build) ...
resource "azurerm_role_assignment" "ci_acr" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}

# ... and deploy to the cluster (kubectl via --admin kubeconfig).
resource "azurerm_role_assignment" "ci_aks" {
  scope                = azurerm_kubernetes_cluster.aks.id
  role_definition_name = "Azure Kubernetes Service Cluster Admin Role"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
}
