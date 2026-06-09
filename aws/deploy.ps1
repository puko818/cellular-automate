# Sets up AWS infrastructure (one-time, safe to re-run).
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
    [string]$Region        = "us-east-1",
    [string]$RepoName      = "cellular-automata",
    [string]$AppName       = "cellular-automata",
    [string]$EnvName       = "cellular-automata-env",
    [string]$CodeBuildRole = "CodeBuildDeployRole",
    [string]$Ec2Role       = "CellularAutomataEC2Role",
    [string]$Ec2Profile    = "CellularAutomataEC2Profile"
)

function Invoke-Aws {
    aws @args
    if ($LASTEXITCODE -ne 0) { Write-Error "aws command failed (exit $LASTEXITCODE). Stopping."; exit $LASTEXITCODE }
}

function Write-TempJson($Object) {
    $f = [System.IO.Path]::GetTempFileName()
    # Out-File -Encoding utf8 writes a BOM in PS 5.1 which AWS CLI rejects as invalid JSON
    [System.IO.File]::WriteAllText($f, ($Object | ConvertTo-Json -Depth 10 -Compress))
    return $f
}

$AccountId = aws sts get-caller-identity --query Account --output text
$EbBucket  = "cellular-automata-eb-$AccountId"

# ECR repository
aws ecr describe-repositories --repository-names $RepoName --region $Region 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Creating ECR repository '$RepoName'..."
    Invoke-Aws ecr create-repository --repository-name $RepoName --region $Region | Out-Null
} else {
    Write-Host "==> ECR repository '$RepoName' already exists, skipping."
}

# S3 bucket for EB deployment bundles
aws s3api head-bucket --bucket $EbBucket 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Creating S3 bucket '$EbBucket'..."
    if ($Region -eq "us-east-1") {
        # us-east-1 does not accept a LocationConstraint
        Invoke-Aws s3api create-bucket --bucket $EbBucket --region $Region | Out-Null
    } else {
        Invoke-Aws s3api create-bucket --bucket $EbBucket --region $Region `
            --create-bucket-configuration LocationConstraint=$Region | Out-Null
    }
} else {
    Write-Host "==> S3 bucket '$EbBucket' already exists, skipping."
}

# EC2 instance profile — lets EB instances pull the Docker image from ECR
aws iam get-role --role-name $Ec2Role 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Creating EC2 instance profile for Elastic Beanstalk..."
    $Ec2Trust = Write-TempJson @{
        Version   = "2012-10-17"
        Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "ec2.amazonaws.com" }; Action = "sts:AssumeRole" })
    }
    Invoke-Aws iam create-role --role-name $Ec2Role --assume-role-policy-document "file://$Ec2Trust" | Out-Null
    Invoke-Aws iam attach-role-policy --role-name $Ec2Role --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly | Out-Null
    Invoke-Aws iam attach-role-policy --role-name $Ec2Role --policy-arn arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier | Out-Null
    Invoke-Aws iam create-instance-profile --instance-profile-name $Ec2Profile | Out-Null
    Invoke-Aws iam add-role-to-instance-profile --instance-profile-name $Ec2Profile --role-name $Ec2Role | Out-Null
} else {
    Write-Host "==> EC2 instance profile already exists, skipping."
}

# CodeBuild IAM role
aws iam get-role --role-name $CodeBuildRole 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Creating IAM role for CodeBuild..."
    $CodeBuildTrust = Write-TempJson @{
        Version   = "2012-10-17"
        Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "codebuild.amazonaws.com" }; Action = "sts:AssumeRole" })
    }
    Invoke-Aws iam create-role --role-name $CodeBuildRole --assume-role-policy-document "file://$CodeBuildTrust" | Out-Null
} else {
    Write-Host "==> IAM role '$CodeBuildRole' already exists, skipping."
}
# Always ensure all required policies are attached (safe to re-run — attach-role-policy is idempotent)
Write-Host "==> Ensuring CodeBuild role policies are attached..."
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/AdministratorAccess-AWSElasticBeanstalk | Out-Null
Invoke-Aws iam attach-role-policy --role-name $CodeBuildRole --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess | Out-Null
$CodeBuildRoleArn = aws iam get-role --role-name $CodeBuildRole --query Role.Arn --output text

# GitHub credentials for CodeBuild
Write-Host "==> Connecting GitHub account to CodeBuild..."
Invoke-Aws codebuild import-source-credentials `
    --server-type GITHUB `
    --auth-type PERSONAL_ACCESS_TOKEN `
    --token $GitHubToken | Out-Null

# CodeBuild project — always create-or-update so env vars stay in sync
$ProjectConfig = @{
    name        = $AppName
    source      = @{ type = "GITHUB"; location = $GitHubRepoUrl; buildspec = "buildspec.yml" }
    environment = @{
        type                 = "LINUX_CONTAINER"
        image                = "aws/codebuild/standard:7.0"
        computeType          = "BUILD_GENERAL1_SMALL"
        privilegedMode       = $true
        environmentVariables = @(
            @{ name = "ECR_REPOSITORY"; value = $RepoName }
            @{ name = "APP_NAME";       value = $AppName }
            @{ name = "EB_ENV_NAME";    value = $EnvName }
            @{ name = "EB_BUCKET";      value = $EbBucket }
        )
    }
    serviceRole = $CodeBuildRoleArn
    artifacts   = @{ type = "NO_ARTIFACTS" }
}
$Existing = aws codebuild batch-get-projects --names $AppName --query "projects[0].name" --output text 2>$null
if ($Existing -eq "None" -or [string]::IsNullOrWhiteSpace($Existing)) {
    Write-Host "==> Creating CodeBuild project..."
    Invoke-Aws codebuild create-project --cli-input-json "file://$(Write-TempJson $ProjectConfig)" | Out-Null
} else {
    Write-Host "==> Updating CodeBuild project '$AppName'..."
    Invoke-Aws codebuild update-project --cli-input-json "file://$(Write-TempJson $ProjectConfig)" | Out-Null
}

# Elastic Beanstalk application
$EbApp = aws elasticbeanstalk describe-applications --application-names $AppName --query "Applications[0].ApplicationName" --output text 2>$null
if ($EbApp -eq "None" -or [string]::IsNullOrWhiteSpace($EbApp)) {
    Write-Host "==> Creating Elastic Beanstalk application..."
    Invoke-Aws elasticbeanstalk create-application --application-name $AppName | Out-Null
} else {
    Write-Host "==> EB application '$AppName' already exists, skipping."
}

# Elastic Beanstalk environment
$EbEnvStatus = aws elasticbeanstalk describe-environments --environment-names $EnvName --query "Environments[0].Status" --output text 2>$null
if ($EbEnvStatus -eq "None" -or [string]::IsNullOrWhiteSpace($EbEnvStatus)) {
    Write-Host "==> Getting latest Docker solution stack..."
    $SolutionStack = aws elasticbeanstalk list-available-solution-stacks `
        --query "SolutionStacks[?contains(@,'running Docker')]|[0]" --output text

    Write-Host "==> Creating Elastic Beanstalk environment (takes ~5 minutes)..."
    $EbOptions = Write-TempJson @(
        @{ Namespace = "aws:autoscaling:launchconfiguration"; OptionName = "IamInstanceProfile"; Value = $Ec2Profile }
        @{ Namespace = "aws:ec2:instances";                   OptionName = "InstanceTypes";       Value = "t3.micro" }
    )
    Invoke-Aws elasticbeanstalk create-environment `
        --application-name $AppName `
        --environment-name $EnvName `
        --solution-stack-name $SolutionStack `
        --option-settings "file://$EbOptions" | Out-Null

    Write-Host "==> Waiting for environment to become ready..."
    do {
        Start-Sleep -Seconds 30
        $EbEnvStatus = aws elasticbeanstalk describe-environments `
            --environment-names $EnvName --query "Environments[0].Status" --output text
        Write-Host "    Status: $EbEnvStatus"
    } while ($EbEnvStatus -ne "Ready")
} else {
    Write-Host "==> EB environment '$EnvName' already exists, skipping."
}

$EbUrl = aws elasticbeanstalk describe-environments `
    --environment-names $EnvName --query "Environments[0].CNAME" --output text

Write-Host ""
Write-Host "Infrastructure ready!"
Write-Host ""
Write-Host "To build and deploy at any time, run:"
Write-Host "  aws codebuild start-build --project-name $AppName --region $Region"
Write-Host ""
Write-Host "App URL (after first deploy): http://$EbUrl"
