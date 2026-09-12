#!/usr/bin/env bash
set -euo pipefail

EXISTING=$(aws cloudformation describe-stacks \
  --stack-name "${ENVIRONMENT_NAME}-root" \
  --query "Stacks[0].Parameters[?ParameterKey=='InitialImageUri'].ParameterValue | [0]" \
  --output text 2>/dev/null || true)

if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  echo "image_uri=$EXISTING" >> "$GITHUB_OUTPUT"
  exit 0
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TAG=$(aws ecr describe-images \
  --repository-name "$ECR_REPOSITORY_NAME" \
  --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' \
  --output text 2>/dev/null || true)

if [ -z "$TAG" ] || [ "$TAG" = "None" ]; then
  echo "image_uri=" >> "$GITHUB_OUTPUT"
else
  echo "image_uri=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY_NAME}:${TAG}" >> "$GITHUB_OUTPUT"
fi
