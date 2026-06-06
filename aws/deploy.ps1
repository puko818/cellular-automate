# Sets up AWS infrastructure (one-time).
# After running this script, trigger builds with:
#   aws codebuild start-build --project-name cellular-automata
#
# Prerequisites:
#   - AWS CLI installed (https://aws.amazon.com/cli/) and configured: aws configure
#   - A GitHub personal access token with repo scope (https://github.com/settings/tokens)
#
# Usage:
#   .\deploy.ps1 -GitHubRepoUrl https://github.com/you/your-repo -GitHubToken ghp_xxx
#   .\deploy.ps1 -GitHubRepoUrl https://github.com/you/your-repo -GitHubToken ghp_xxx -Region eu-west-1

param(
    [Parameter(Mandatory)][string]$GitHubRepoUrl,
    [Parameter(Mandatory)][string]$GitHubToken,
    [string]$Region          = "us-east-1",
    [string]$RepoName        = "cellular-automata",
    [string]$ServiceName     = "cellular-automata",
    [string]$AppRunnerRole   = "AppRunnerECRAccessRole",
    [string]$CodeBuildRole   = "CodeBuildDeployRole"
)

function Invoke-Aws {
    aws @args
    if ($LASTEXITCODE -ne 0) { Write-Error "aws command failed (exit $LASTEXITCODE). Stopping."; exit $LASTEXITCODE }
}

function Write-TempJson($Object) {
    $f = [System.IO.Path]::GetTempFileName()
    ($Object | ConvertTo-Json -Depth 10) | Out-File -FilePath $f -Encoding utf8
    return $f
}

Write-Host "==> Creating ECR repository '$RepoName'..."
Invoke-Aws ecr create-repository --repository-name $RepoName --region $Region | Out-Null

Write-Host "==> Creating IAM role so App Runner can pull from ECR..."
$AppRunnerTrust = Write-TempJson @{
    Version   = "2012-10-17"
    Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "build.apprunner.amazonaws.com" }; Action = "sts:AssumeRole" })
}
Invoke-Aws iam create-role --role-name $AppRunnerRole --assume-role-policy-document "file://$AppRunnerTrust" | Out-Null
Invoke-Aws iam attach-role-policy --role-name $AppRunnerRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess | Out-Null
$AppRunnerRoleArn = aws iam get-role --role-name $AppRunnerRole --query Role.Arn -o text

Write-Host "==> Creating IAM role for CodeBuild..."
$CodeBuildTrust = Write-TempJson @{
    Version   = "2012-10-17"
    Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "codebuild.amazonaws.com" }; Action = "sts:AssumeRole" })
}
Invoke-Aws iam create-role --role-name $CodeBuildRole --assume-role-policy-document "file://$CodeBuildTrust" | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/AWSAppRunnerFullAccess | Out-Null
$CodeBuildRoleArn = aws iam get-role --role-name $CodeBuildRole --query Role.Arn -o text

Write-Host "==> Connecting GitHub account to CodeBuild..."
Invoke-Aws codebuild import-source-credentials `
    --server-type GITHUB `
    --auth-type PERSONAL_ACCESS_TOKEN `
    --token $GitHubToken | Out-Null

Write-Host "==> Creating CodeBuild project..."
$Project = Write-TempJson @{
    name        = $ServiceName
    source      = @{
        type      = "GITHUB"
        location  = $GitHubRepoUrl
        buildspec = "buildspec.yml"
    }
    environment = @{
        type                  = "LINUX_CONTAINER"
        image                 = "aws/codebuild/standard:7.0"
        computeType           = "BUILD_GENERAL1_SMALL"
        privilegedMode        = $true
        environmentVariables  = @(
            @{ name = "ECR_REPOSITORY";     value = $RepoName }
            @{ name = "SERVICE_NAME";       value = $ServiceName }
            @{ name = "APPRUNNER_ROLE_ARN"; value = $AppRunnerRoleArn }
        )
    }
    serviceRole = $CodeBuildRoleArn
    artifacts   = @{ type = "NO_ARTIFACTS" }
}
Invoke-Aws codebuild create-project --cli-input-json "file://$Project" | Out-Null

Write-Host ""
Write-Host "Infrastructure ready!"
Write-Host ""
Write-Host "To build and deploy at any time, run:"
Write-Host "  aws codebuild start-build --project-name $ServiceName --region $Region"
Write-Host ""
Write-Host "Build logs: https://$Region.console.aws.amazon.com/codesuite/codebuild/projects/$ServiceName"
