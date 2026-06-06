# Sets up Azure infrastructure (one-time).
# Image building and deployment is handled by GitHub Actions - see .github/workflows/deploy.yml.
#
# Prerequisites:
#   - az CLI installed and logged in: az login
#
# Usage:
#   .\deploy.ps1
#   .\deploy.ps1 -ResourceGroup my-rg -Location westeurope -AcrName myuniqueacr

param(
    [string]$ResourceGroup = "cellular-automata-rg",
    [string]$Location      = "eastus",
    [string]$AcrName       = "cellautomataacr",   # globally unique, lowercase alphanumeric only
    [string]$AppName       = "cellular-automata",
    [string]$EnvName       = "cellular-automata-env"
)

$SubscriptionId = az account show --query "id" -o tsv

function Invoke-Az {
    az @args
    if ($LASTEXITCODE -ne 0) { Write-Error "az command failed (exit $LASTEXITCODE). Stopping."; exit $LASTEXITCODE }
}

Write-Host "==> Registering required Azure resource providers (one-time, may take a minute)..."
Invoke-Az provider register -n Microsoft.ContainerRegistry   --wait
Invoke-Az provider register -n Microsoft.App                 --wait
Invoke-Az provider register -n Microsoft.OperationalInsights --wait

Write-Host "==> Creating resource group '$ResourceGroup'..."
Invoke-Az group create --name $ResourceGroup --location $Location | Out-Null

Write-Host "==> Creating Azure Container Registry '$AcrName'..."
Invoke-Az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic --admin-enabled true | Out-Null

Write-Host "==> Creating Container Apps environment '$EnvName'..."
Invoke-Az containerapp env create `
    --name $EnvName `
    --resource-group $ResourceGroup `
    --location $Location | Out-Null

Write-Host "==> Retrieving ACR credentials..."
$AcrServer   = "$AcrName.azurecr.io"
$AcrUsername = az acr credential show --name $AcrName --query "username" -o tsv
$AcrPassword = az acr credential show --name $AcrName --query "passwords[0].value" -o tsv

Write-Host "==> Creating service principal for GitHub Actions..."
$SpJson = az ad sp create-for-rbac `
    --name "$AppName-deploy" `
    --role contributor `
    --scopes "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup" `
    -o json
if ($LASTEXITCODE -ne 0) { Write-Error "Service principal creation failed."; exit 1 }

$Sp = $SpJson | ConvertFrom-Json
$CredsHash = [ordered]@{
    clientId       = $Sp.appId
    clientSecret   = $Sp.password
    subscriptionId = $SubscriptionId
    tenantId       = $Sp.tenant
}
$AzureCreds = $CredsHash | ConvertTo-Json

Write-Host ""
Write-Host "Infrastructure ready. Add these 4 secrets to your GitHub repo:"
Write-Host "(Settings > Secrets and variables > Actions > New repository secret)"
Write-Host ""
Write-Host "  ACR_LOGIN_SERVER = $AcrServer"
Write-Host "  ACR_USERNAME     = $AcrUsername"
Write-Host "  ACR_PASSWORD     = $AcrPassword"
Write-Host ""
Write-Host "  AZURE_CREDENTIALS = (copy the JSON block below as-is)"
Write-Host $AzureCreds
Write-Host ""
Write-Host "Then push to main - GitHub Actions will build and deploy automatically."
