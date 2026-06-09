"""
Sets up AWS infrastructure (one-time, safe to re-run).
After running this script, trigger builds with:
  aws codebuild start-build --project-name cellular-automata

Prerequisites:
  pip install boto3
  AWS credentials configured (aws configure or environment variables)
  A GitHub personal access token with repo scope

Usage:
  python deploy.py --github-repo-url https://github.com/you/your-repo --github-token ghp_xxx
  python deploy.py --github-repo-url https://github.com/you/your-repo --github-token ghp_xxx --region eu-west-1
"""

import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError


def parse_args():
    p = argparse.ArgumentParser(description="Provision AWS infrastructure for cellular-automata.")
    p.add_argument("--github-repo-url", required=True)
    p.add_argument("--github-token", required=True)
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--repo-name", default="cellular-automata")
    p.add_argument("--app-name", default="cellular-automata")
    p.add_argument("--env-name", default="cellular-automata-env")
    p.add_argument("--codebuild-role", default="CodeBuildDeployRole")
    p.add_argument("--ec2-role", default="CellularAutomataEC2Role")
    p.add_argument("--ec2-profile", default="CellularAutomataEC2Profile")
    return p.parse_args()


def ensure_ecr_repo(ecr, repo_name):
    try:
        ecr.describe_repositories(repositoryNames=[repo_name])
        print(f"==> ECR repository '{repo_name}' already exists, skipping.")
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        print(f"==> Creating ECR repository '{repo_name}'...")
        ecr.create_repository(repositoryName=repo_name)


def ensure_s3_bucket(s3, bucket, region):
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"==> S3 bucket '{bucket}' already exists, skipping.")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        print(f"==> Creating S3 bucket '{bucket}'...")
        kwargs = {"Bucket": bucket}
        # us-east-1 rejects a LocationConstraint
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)


def ensure_ec2_instance_profile(iam, ec2_role, ec2_profile):
    try:
        iam.get_role(RoleName=ec2_role)
        print("==> EC2 instance profile already exists, skipping.")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    print("==> Creating EC2 instance profile for Elastic Beanstalk...")
    trust = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })
    iam.create_role(RoleName=ec2_role, AssumeRolePolicyDocument=trust)
    iam.attach_role_policy(
        RoleName=ec2_role,
        PolicyArn="arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    )
    iam.attach_role_policy(
        RoleName=ec2_role,
        PolicyArn="arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier",
    )
    iam.create_instance_profile(InstanceProfileName=ec2_profile)
    iam.add_role_to_instance_profile(InstanceProfileName=ec2_profile, RoleName=ec2_role)


def ensure_codebuild_role(iam, role_name):
    try:
        iam.get_role(RoleName=role_name)
        print(f"==> IAM role '{role_name}' already exists, skipping.")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print("==> Creating IAM role for CodeBuild...")
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust)

    print("==> Ensuring CodeBuild role policies are attached...")
    for arn in [
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser",
        "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        "arn:aws:iam::aws:policy/AdministratorAccess-AWSElasticBeanstalk",
        "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    ]:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)

    return iam.get_role(RoleName=role_name)["Role"]["Arn"]


def connect_github(cb, github_repo_url, github_token):
    print("==> Connecting GitHub account to CodeBuild...")
    cb.import_source_credentials(
        serverType="GITHUB",
        authType="PERSONAL_ACCESS_TOKEN",
        token=github_token,
    )


def upsert_codebuild_project(cb, app_name, github_repo_url, env_name, eb_bucket, role_arn):
    project_config = {
        "name": app_name,
        "source": {
            "type": "GITHUB",
            "location": github_repo_url,
            "buildspec": "buildspec.yml",
        },
        "environment": {
            "type": "LINUX_CONTAINER",
            "image": "aws/codebuild/standard:7.0",
            "computeType": "BUILD_GENERAL1_SMALL",
            "privilegedMode": True,
            "environmentVariables": [
                {"name": "ECR_REPOSITORY", "value": app_name},
                {"name": "APP_NAME",       "value": app_name},
                {"name": "EB_ENV_NAME",    "value": env_name},
                {"name": "EB_BUCKET",      "value": eb_bucket},
            ],
        },
        "serviceRole": role_arn,
        "artifacts": {"type": "NO_ARTIFACTS"},
    }

    result = cb.batch_get_projects(names=[app_name])
    if result["projects"]:
        print(f"==> Updating CodeBuild project '{app_name}'...")
        cb.update_project(**project_config)
    else:
        print("==> Creating CodeBuild project...")
        cb.create_project(**project_config)


def ensure_eb_application(eb, app_name):
    result = eb.describe_applications(ApplicationNames=[app_name])
    if result["Applications"]:
        print(f"==> EB application '{app_name}' already exists, skipping.")
    else:
        print("==> Creating Elastic Beanstalk application...")
        eb.create_application(ApplicationName=app_name)


def ensure_eb_environment(eb, app_name, env_name, ec2_profile):
    result = eb.describe_environments(EnvironmentNames=[env_name])
    envs = [e for e in result["Environments"] if e["Status"] != "Terminated"]
    if envs:
        print(f"==> EB environment '{env_name}' already exists, skipping.")
        return envs[0]["CNAME"]

    print("==> Getting latest Docker solution stack...")
    stacks = eb.list_available_solution_stacks()["SolutionStacks"]
    docker_stack = next(s for s in stacks if "running Docker" in s)

    print("==> Creating Elastic Beanstalk environment (takes ~5 minutes)...")
    eb.create_environment(
        ApplicationName=app_name,
        EnvironmentName=env_name,
        SolutionStackName=docker_stack,
        OptionSettings=[
            {
                "Namespace": "aws:autoscaling:launchconfiguration",
                "OptionName": "IamInstanceProfile",
                "Value": ec2_profile,
            },
            {
                "Namespace": "aws:ec2:instances",
                "OptionName": "InstanceTypes",
                "Value": "t3.micro",
            },
        ],
    )

    print("==> Waiting for environment to become ready...")
    while True:
        time.sleep(30)
        envs = eb.describe_environments(EnvironmentNames=[env_name])["Environments"]
        status = envs[0]["Status"] if envs else "Unknown"
        print(f"    Status: {status}")
        if status == "Ready":
            return envs[0]["CNAME"]


def main():
    args = parse_args()

    session = boto3.Session(region_name=args.region)
    sts = session.client("sts")
    ecr = session.client("ecr")
    s3  = session.client("s3")
    iam = session.client("iam")
    cb  = session.client("codebuild")
    eb  = session.client("elasticbeanstalk")

    account_id = sts.get_caller_identity()["Account"]
    eb_bucket  = f"cellular-automata-eb-{account_id}"

    ensure_ecr_repo(ecr, args.repo_name)
    ensure_s3_bucket(s3, eb_bucket, args.region)
    ensure_ec2_instance_profile(iam, args.ec2_role, args.ec2_profile)
    role_arn = ensure_codebuild_role(iam, args.codebuild_role)
    connect_github(cb, args.github_repo_url, args.github_token)
    upsert_codebuild_project(cb, args.app_name, args.github_repo_url, args.env_name, eb_bucket, role_arn)
    ensure_eb_application(eb, args.app_name)
    cname = ensure_eb_environment(eb, args.app_name, args.env_name, args.ec2_profile)

    print()
    print("Infrastructure ready!")
    print()
    print("To build and deploy at any time, run:")
    print(f"  aws codebuild start-build --project-name {args.app_name} --region {args.region}")
    print()
    print(f"App URL (after first deploy): http://{cname}")


if __name__ == "__main__":
    main()
