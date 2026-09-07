# todo-infra

CloudFormation infrastructure for the To-Do app: multi-AZ VPC, RDS PostgreSQL (Multi-AZ) behind
RDS Proxy, ElastiCache Redis (replication group with automatic failover), ECS Fargate + ALB, and
the CodePipeline/CodeDeploy blue/green deployment pipeline triggered by EventBridge on ECR image
push.

## Stack architecture: real nested stacks, not sibling stacks

`templates/root.yaml` is the root template. It nests `network.yaml`, `ecr.yaml`, `data.yaml`,
`cache.yaml`, `ecs.yaml`, and `pipeline.yaml` as actual `AWS::CloudFormation::Stack` children —
each one is created and updated as part of the root stack's own deploy, and receives its inputs
as CloudFormation Parameters passed down from the root (sourced via `Fn::GetAtt` on an earlier
sibling's `Outputs`), not via `Fn::ImportValue`/`Export`. `templates/bootstrap.yaml` is the one
exception: it is deployed separately and first, because nothing can package or deploy the root
stack before the IAM role and S3 template-staging bucket it creates exist.

Because the child templates are local files, deploying the root stack is a two-step process:

```bash
aws cloudformation package \
  --template-file templates/root.yaml \
  --s3-bucket <TemplateBucketName> \
  --s3-prefix cfn-templates \
  --output-template-file packaged-root.yaml

aws cloudformation deploy \
  --stack-name todo-dev-root \
  --template-file packaged-root.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides EnvironmentName=todo-dev ECRRepositoryName=todo-app ArtifactBucketName=<...>
```

`package` uploads each local child template to S3 and rewrites root.yaml's relative
`TemplateURL: ./network.yaml`-style references into real S3 URLs; `deploy` then creates/updates
the root stack and, transitively, every nested child.

## Design decisions worth knowing before you touch this

- **No CloudFormation Git Sync.** Infra is deployed by a GitHub Actions workflow running
  `aws cloudformation package`/`deploy` under OIDC, not the native CloudFormation Git Sync
  feature. Deliberate, not an oversight: Git Sync caused hard-to-diagnose failures in a prior lab
  (permanent sync blockers from out-of-band stack changes, no support for *initial* stack
  creation, console-invisible config issues). See `../AWS_LAB_BEST_PRACTICES.md`. If graded
  strictly against the literal "GitSync" rubric line, note this substitution explicitly in the
  submission.
- **No NAT Gateway.** Private subnets have no internet route at all. All egress the ECS tasks
  need (ECR image pulls, CloudWatch Logs, Secrets Manager, S3 for image layers) goes through VPC
  interface/gateway endpoints instead.
- **CodePipeline's Source stage is S3-only, not ECR.** The ECR repo uses immutable git-SHA tags
  (no floating `:latest`), which CodePipeline's native ECR source action can't watch (it tracks
  one fixed tag). Instead, `todo-app`'s workflow renders the real image URI straight into
  `taskdef.json` and uploads it with `appspec.yaml` to S3 on every build. EventBridge — watching
  the actual ECR push event — is what starts the pipeline.
- Full HA: RDS Multi-AZ standby + Redis replica. No AUTH token / TLS on Redis (private-subnet +
  security-group isolation only) — a documented lab-scope simplification.

## One-time bootstrap (do this before the workflow can run at all)

`bootstrap.yaml` creates the IAM role the GitHub Actions workflow assumes and the S3 bucket
`cloudformation package` stages templates in — so it has to be deployed once with your own
local/console credentials before CI can take over:

```bash
aws cloudformation deploy \
  --stack-name todo-dev-bootstrap \
  --template-file templates/bootstrap.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    EnvironmentName=todo-dev \
    GitHubOwnerId=<gh api users/<owner> --jq .id> \
    InfraRepositoryId=<gh api repos/<owner>/todo-infra --jq .id> \
    AppRepositoryId=<gh api repos/<owner>/todo-app --jq .id> \
    ECRRepositoryName=todo-app \
    ArtifactBucketName=todo-app-pipeline-artifacts-<your-account-id> \
    TemplateBucketName=todo-app-cfn-templates-<your-account-id>
```

`GitHubOwnerId`/`*RepositoryId` are numeric IDs, not names — they're stable across org/repo
renames, unlike a `sub` claim built from the "owner/repo" string.

## Required GitHub repo configuration (`todo-infra`)

**Secrets** (masked — ARNs/account IDs/repo IDs/bucket names, per best-practice: identifiers stay
in Secrets, never Variables):
`INFRA_DEPLOY_ROLE_ARN`, `GITHUB_OWNER_ID`, `INFRA_REPOSITORY_ID`, `APP_REPOSITORY_ID`,
`ARTIFACT_BUCKET_NAME`, `TEMPLATE_BUCKET_NAME`

**Variables**: `AWS_REGION` (e.g. `us-east-1`), `ECR_REPOSITORY_NAME` (`todo-app`)

## After the root stack deploys: hand outputs to `todo-app`

The `todo-app` repo has no access to these CFN outputs — collect them once and set as secrets
there (see `todo-app/README.md`). Everything now comes from the single root stack:

```bash
aws cloudformation describe-stacks --stack-name todo-dev-root --query "Stacks[0].Outputs"
```

## Verifying a deploy

```bash
aws cloudformation describe-stacks --stack-name todo-dev-root \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue" --output text
aws cloudformation list-stack-resources --stack-name todo-dev-root  # see the nested child stacks
aws ecs describe-services --cluster todo-dev-cluster --services todo-dev-todo-app
```

The ECS target group will show unhealthy until the first real app image is deployed by
CodeDeploy — the `ecs` child stack bootstraps the service with a public placeholder image since
the ECR repo is empty at first deploy. That's expected, not a bug.

Known nested-stack blast-radius tradeoff: because everything but `bootstrap` lives under one
root stack, a failed update to any single child (say, a bad `ecs.yaml` change) rolls back the
whole root stack update, not just that child. This is inherent to real nested stacks — it's the
tradeoff for "one deploy creates/updates everything together," which is what was asked for here.
