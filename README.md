# todo-infra

CloudFormation infrastructure for the To-Do app: multi-AZ VPC, RDS PostgreSQL (Multi-AZ) behind
RDS Proxy, ElastiCache Redis (replication group with automatic failover), ECS Fargate + ALB, and
the CodePipeline/CodeDeploy blue/green deployment pipeline triggered by EventBridge on ECR image
push. The ECR repository itself lives in a separate repo — see `todo-ecr` — deployed and
versioned independently of everything here.

## Network architecture diagram

`diagrams/architecture.drawio` is the deliverable — open it directly at app.diagrams.net (File
-> Open From -> Device) or the desktop app. It follows the standard AWS reference-architecture
nesting convention (AWS Cloud -> Region -> VPC -> Availability Zone -> Subnet) using the official
AWS4 icon shapes built into draw.io, not a rendered image or an AI-interpreted text prompt.

Region-level (outside the VPC — these are regional services, not VPC-resident) is laid out as a
2x2 grid of clearly separated groups: **Bootstrap** (OIDC provider, all three IAM roles, template
bucket), **ECR Repository** (its own repo and stack — `todo-ecr` — shown standalone rather than
lumped into the pipeline group), **CI/CD Pipeline** (EventBridge rule, artifact bucket, CodePipeline, CodeDeploy),
and **Configuration & Secrets** (both Secrets Manager secrets, all 5 SSM parameters). Each
Availability Zone is its own 2x2 grid of subnets (Public/ECS on top, Data/Cache below) — every
resource label carries real detail (instance class, IAM role names, security group names, ports,
storage size, auto-scaling config) rather than just a service name.

`diagrams/generate_drawio.py` is the diagram-as-code source that produces it — a small
stack/grid layout algorithm rather than hand-placed coordinates, so spacing stays consistent as
detail is added. Rerun it after any architecture change:

```bash
cd diagrams && python3 generate_drawio.py
```

`diagrams/architecture.png`/`architecture.py` are an earlier, simpler rendering (Python
`diagrams` library) kept as a secondary reference; the `.drawio` file is the primary deliverable.

## Stack architecture: real nested stacks, not sibling stacks

`templates/root.yaml` is the root template. It nests `network.yaml`, `data.yaml`, `cache.yaml`,
`ecs.yaml`, and `pipeline.yaml` as actual `AWS::CloudFormation::Stack` children — each one is
created and updated as part of the root stack's own deploy, and receives its inputs as
CloudFormation Parameters passed down from the root (sourced via `Fn::GetAtt` on an earlier
sibling's `Outputs`), not via `Fn::ImportValue`/`Export`. Two things are deliberately *not*
nested here: `templates/bootstrap.yaml` (deployed separately, first, because nothing can package
or deploy the root stack before the IAM role and S3 template-staging bucket it creates exist),
and ECR itself, which lives in the separate `todo-ecr` repo/stack — root.yaml only ever
references it by *name* (`ECRRepositoryName`), never creates it or nests it.

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

## No placeholder image, ever — EcsStack/PipelineStack are conditional

`root.yaml` takes an `InitialImageUri` parameter (default `""`) and a `HasInitialImage`
condition. `EcsStack` and `PipelineStack` only exist when that condition is true. So:

1. **First deploy**: leave `InitialImageUri` empty. Only `NetworkStack`/`DataStack`/
   `CacheStack` get created — no ECS, no ALB, no pipeline, and critically, no fake placeholder
   image anywhere. (The ECR repository itself needs to exist first too — see `todo-ecr`'s
   README; its deploy is fully independent of this one.)
2. **One-time manual bootstrap push** (see below): build and push the *real* app image once,
   by hand, so ECR actually has a tag to reference.
3. **Redeploy `root.yaml`** with `InitialImageUri` set to that real image URI. This creates
   `EcsStack`/`PipelineStack` referencing a real image from the start — no busybox/httpd
   placeholder, no `DesiredCount: 0` + manual bump, no bootstrap dance to design around.

`deploy-infra.yml` automates step 3: on every run it looks up the newest pushed ECR tag and
passes it as `InitialImageUri` automatically (resolving to empty until step 2 has happened, at
which point the very next infra deploy creates the compute stacks on its own).

### One-time bootstrap push

After step 1 (foundation-only deploy) has created the ECR repo, push a real image once with
your own local credentials — this becomes the ECS service's first task definition:

```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
cd ../todo-app
docker build -t <account-id>.dkr.ecr.<region>.amazonaws.com/todo-app:bootstrap .
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/todo-app:bootstrap
```

Then re-run (or `workflow_dispatch`) the infra deploy workflow — it'll find that tag and create
`EcsStack`/`PipelineStack`. From then on, every push to `todo-app` goes through the real
EventBridge → CodePipeline → CodeDeploy blue/green path.

## Design decisions worth knowing before you touch this

- **No CloudFormation Git Sync.** Infra is deployed by a GitHub Actions workflow running
  `aws cloudformation package`/`deploy` under OIDC, not the native CloudFormation Git Sync
  feature. Deliberate, not an oversight: Git Sync caused hard-to-diagnose failures in a prior lab
  (permanent sync blockers from out-of-band stack changes, no support for *initial* stack
  creation, console-invisible config issues). See `../AWS_LAB_BEST_PRACTICES.md`. If graded
  strictly against the literal "GitSync" rubric line, note this substitution explicitly in the
  submission.
- **No NAT Gateway.** Private subnets have no internet route at all. All egress the ECS tasks
  need (ECR image pulls, CloudWatch Logs, Secrets Manager, SSM, S3 for image layers) goes
  through VPC interface/gateway endpoints instead.
- **CodePipeline's Source stage is S3-only, not ECR.** The ECR repo uses immutable git-SHA tags
  (no floating `:latest`), which CodePipeline's native ECR source action can't watch (it tracks
  one fixed tag). Instead, `todo-app`'s workflow generates the real task definition (image URI
  baked in) directly and uploads it with `appspec.yaml` to S3 on every build. EventBridge —
  watching the actual ECR push event — is what starts the pipeline.
- **Only real secrets live in Secrets Manager.** DB credentials (RDS-managed) and the Django
  secret key are genuine secrets with unpredictable names/ARNs, so they're in Secrets Manager and
  flow through CloudFormation `Fn::GetAtt`/Outputs. Everything else the app needs — DB proxy
  endpoint, DB port/name, Redis host/port — is plain config, not a secret: it lives in SSM
  Parameter Store (free for Standard-tier parameters, vs. Secrets Manager's per-secret cost)
  under a deterministic `/<EnvironmentName>/...` path, and the ECS task definition references it
  by that predictable name — no need to pipe it through nested-stack Parameters or GitHub
  secrets at all (see `data.yaml`/`cache.yaml`/`ecs.yaml`, and `todo-app/README.md`).
- **No checked-in task-definition template.** `todo-app`'s workflow builds the real, complete
  task definition JSON with `jq` on every run (image URI + the same naming-convention values
  `ecs.yaml` uses) rather than `sed`/`envsubst`-substituting placeholder tokens into a committed
  `taskdef.json`. One fewer place a deploy can silently go stale.
- Full HA: RDS Multi-AZ standby + Redis replica. No AUTH token / TLS on Redis (private-subnet +
  security-group isolation only) — a documented lab-scope simplification.
- **ECR lives in its own repo (`todo-ecr`), not nested under root.yaml.** It has no VPC/network
  dependency and a completely independent lifecycle from the rest of the infrastructure — a
  bad `ecs.yaml` change rolling back the root stack should never be able to touch the image
  repository holding every previously-built image. Its own dedicated OIDC role (`EcrDeployRole`,
  created here in `bootstrap.yaml` alongside the other two) keeps its permissions scoped to
  exactly that one stack and that one repository.

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
    EcrRepositoryId=<gh api repos/<owner>/todo-ecr --jq .id> \
    ECRRepositoryName=todo-app \
    ArtifactBucketName=todo-app-pipeline-artifacts-<your-account-id> \
    TemplateBucketName=todo-app-cfn-templates-<your-account-id>
```

`GitHubOwnerId`/`*RepositoryId` are numeric IDs, not names — they're stable across org/repo
renames, unlike a `sub` claim built from the "owner/repo" string. This one bootstrap stack
creates the OIDC roles for all three repos (`todo-infra`, `todo-app`, `todo-ecr`) — extending a
shared bootstrap rather than duplicating one per repo.

## Required GitHub repo configuration (`todo-infra`)

**Secrets** (masked — ARNs/account IDs/repo IDs/bucket names, per best-practice: identifiers stay
in Secrets, never Variables):
`INFRA_DEPLOY_ROLE_ARN`, `GH_OWNER_ID`, `INFRA_REPOSITORY_ID`, `APP_REPOSITORY_ID`,
`ECR_REPOSITORY_ID`, `ARTIFACT_BUCKET_NAME`, `TEMPLATE_BUCKET_NAME`

**Variables** (plain, non-identifying config — also referenced as `vars.*`, never hardcoded as a
literal in the workflow): `ENVIRONMENT_NAME` (`todo-dev`), `AWS_REGION` (e.g. `us-east-1`),
`ECR_REPOSITORY_NAME` (`todo-app`)

## After deploying: hand outputs to `todo-app`

The `todo-app` repo has no access to these CFN outputs — collect them once and set them there
(see `todo-app/README.md` — the list is short, since non-secret config is now resolved by naming
convention / SSM instead of being copied through GitHub). Two different stacks now hold these:

```bash
aws cloudformation describe-stacks --stack-name todo-dev-root --query "Stacks[0].Outputs"
aws cloudformation describe-stacks --stack-name todo-dev-ecr --query "Stacks[0].Outputs"  # RepositoryUri
```

## Verifying a deploy

```bash
aws cloudformation describe-stacks --stack-name todo-dev-root \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue" --output text
aws cloudformation list-stack-resources --stack-name todo-dev-root  # see the nested child stacks
aws cloudformation describe-stacks --stack-name todo-dev-ecr --query "Stacks[0].Outputs"  # separate stack, separate repo
aws ecs describe-services --cluster todo-dev-cluster --services todo-dev-todo-app
aws ssm get-parameters-by-path --path /todo-dev --output table  # confirm plain config landed
```

Known nested-stack blast-radius tradeoff: because everything but `bootstrap` lives under one
root stack, a failed update to any single child (say, a bad `ecs.yaml` change) rolls back the
whole root stack update, not just that child. This is inherent to real nested stacks — it's the
tradeoff for "one deploy creates/updates everything together," which is what was asked for here.

## Real bugs found and fixed on the first actual deploy to this account

Five things only surfaced once this was deployed for real, not caught by `validate-template`:

1. **This AWS account already had a GitHub OIDC provider** (from a prior lab — IAM allows only
   one `token.actions.githubusercontent.com` provider per account). `bootstrap.yaml` no longer
   creates `AWS::IAM::OIDCProvider`; it references the existing provider's deterministic ARN
   (`arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com`) directly.
2. **IAM now requires an OIDC trust policy to include a `sub` or `job_workflow_ref` condition
   that isn't wildcarded to everything**, even when `repository_id`/`repository_owner_id`
   conditions are already present and more durable. Added a `GitHubOwner` parameter and a
   `StringLike` condition on `token.actions.githubusercontent.com:sub` (`repo:<owner>/<repo>:*`)
   alongside the existing ID-based conditions in all three roles.
3. **`AWS::EC2::SecurityGroup`'s `GroupDescription` rejects characters outside
   `a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*`** — an apostrophe in one description broke deployment. It
   also silently rejects the trailing newline a YAML folded scalar (`>`) appends, even when
   every visible character is otherwise valid — descriptions must be plain single-line scalars.
4. **Missing SSM VPC endpoint.** Moving DB/Redis config to SSM Parameter Store (see above) added
   a real network dependency the VPC endpoint list didn't have yet — ECS tasks couldn't reach
   SSM to resolve those `secrets` entries and failed to launch. Added `SsmEndpoint`
   (`com.amazonaws.<region>.ssm`, interface) alongside the other four.
5. **Missing security-group egress path to the S3 gateway endpoint.** Gateway endpoints (unlike
   interface endpoints) have no ENI/security-group of their own — routing traffic to one via the
   route table isn't enough if the security group's egress rules don't also permit it. ECR image
   pulls fetch layer blobs through S3, so without this, pulls timed out even though DNS/routing
   looked correct. Added an `EcsEgressToS3Gateway` rule using `DestinationPrefixListId` (the
   managed prefix list for the S3 endpoint), which is exactly the mechanism this rule type exists
   for — CIDR/security-group destinations can't express "wherever this gateway endpoint routes."
