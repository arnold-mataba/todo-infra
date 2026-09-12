# todo-infra

CloudFormation infrastructure for the To-Do app: multi-AZ VPC, RDS PostgreSQL (Multi-AZ) behind
RDS Proxy, ElastiCache Redis (replication group with automatic failover), ECS Fargate + ALB, and
the CodePipeline/CodeDeploy blue/green deployment pipeline triggered by EventBridge on ECR image
push. Everything one-time/rarely-touched — the GitHub OIDC roles and the ECR repository itself —
lives in a separate repo, `todo-bootstrap`, deployed and versioned independently of everything
here.

## Network architecture diagram

`diagrams/architecture.drawio` is the deliverable — open it directly at app.diagrams.net (File
-> Open From -> Device) or the desktop app. It follows the standard AWS reference-architecture
nesting convention (AWS Cloud -> Region -> VPC -> Availability Zone -> Subnet) using the official
AWS4 icon shapes built into draw.io, not a rendered image or an AI-interpreted text prompt.

Region-level (outside the VPC — these are regional services, not VPC-resident) is laid out as a
2x2 grid of clearly separated groups: **Bootstrap** (OIDC provider, all three IAM roles, template
bucket — both live in `todo-bootstrap`), **ECR Repository** (its own stack in that same repo,
shown standalone rather than lumped into the pipeline group), **CI/CD Pipeline** (EventBridge
rule, artifact bucket, CodePipeline, CodeDeploy), and **Configuration & Secrets** (both Secrets
Manager secrets, all 5 SSM parameters). Each
Availability Zone is its own 2x2 grid of subnets (Public/ECS on top, Data/Cache below) — every
resource label carries real detail (instance class, IAM role names, security group names, ports,
storage size, auto-scaling config) rather than just a service name.

`diagrams/generate_drawio.py` is the diagram-as-code source that produces it — a small
stack/grid layout algorithm rather than hand-placed coordinates, so spacing stays consistent as
detail is added. Rerun it after any architecture change:

```bash
cd diagrams && python3 generate_drawio.py
```

An earlier, simpler rendering (`architecture.py`, Python `diagrams` library, plus its stale
`architecture.png` export) used to be kept alongside this as a "secondary reference" — removed:
it predated most of the icons above (OIDC, all three IAM roles, ECR, both S3 buckets) and having
two diagrams meant one of them was always out of date. `architecture.drawio` is the only
diagram now; regenerate it with the command above, there is nothing else to keep in sync.

## Stack architecture: real nested stacks, not sibling stacks

`templates/root.yaml` is the root template. It nests `network.yaml`, `data.yaml`, `cache.yaml`,
`ecs.yaml`, and `pipeline.yaml` as actual `AWS::CloudFormation::Stack` children — each one is
created and updated as part of the root stack's own deploy, and receives its inputs as
CloudFormation Parameters passed down from the root (sourced via `Fn::GetAtt` on an earlier
sibling's `Outputs`), not via `Fn::ImportValue`/`Export`. Bootstrap (the IAM roles + template
bucket) and ECR are both deliberately *not* nested here — both live in the separate
`todo-bootstrap` repo, deployed before and independently of this stack. Nothing here can even
package or deploy the root stack before that repo's IAM role and S3 template-staging bucket
exist; root.yaml only ever references the ECR repository by *name* (`ECRRepositoryName`), never
creates it or nests it.

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

## No placeholder image, ever — the image is always `:latest`, a fixed mutable tag

`EcsStack`/`PipelineStack` are always created — there's no conditional gating and no
`InitialImageUri` parameter. `ecs.yaml` builds the image URI itself
(`${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/${ECRRepositoryName}:latest`), since
`todo-app`'s workflow always pushes to that same mutable tag — the URI is a fixed, derivable
string, never looked up or passed in from anywhere. The only precondition is that ECR must
already have *an* image under `:latest` before the very first deploy of this stack, since ECS
can't launch a task from a tag that doesn't exist yet:

```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
cd ../todo-app
docker build -t <account-id>.dkr.ecr.<region>.amazonaws.com/todo-app:latest .
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/todo-app:latest
```

After that one-time push, every future deploy of `root.yaml` (including the very first one)
just works — no two-phase dance, no script to look up "the newest tag," nothing to keep in sync.
From then on, every push to `todo-app` overwrites `:latest` and goes through the real
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
- **ECR uses a mutable `:latest` tag, not immutable git-SHA tags.** Every build overwrites the
  same tag, so the image URI is a fixed, derivable string — `ecs.yaml` computes it itself, no
  lookup, no parameter, no script. See `todo-app/README.md` for the tradeoff (no per-build image
  history; rollback means redeploying an older commit, not repointing a tag).
- **CodePipeline's Source stage is still S3, not ECR, even with a floating tag now.**
  CodePipeline's native ECR source action could watch `:latest` today, but it would still need a
  separate mechanism to deliver `appspec.yaml`/`taskdef.json` alongside the trigger (a GitHub
  source action needs a manually-authorized CodeStar Connection — a console-only step). S3 +
  EventBridge delivers both the trigger and the two files together, with no extra manual setup.
- **Both app secrets have deterministic names, not RDS/CFN-assigned random ones.** The Django
  key was always named `${EnvironmentName}-django-secret-key`; the DB credentials secret
  (`data.yaml`'s `DbCredentialsSecret`) is now self-managed the same way, replacing RDS's
  `ManageMasterUserPassword` feature specifically so its name — and therefore a static, partial
  ARN — is knowable ahead of time. Tradeoff: no automatic password rotation (RDS's managed
  passwords rotate themselves; a self-managed secret needs its own rotation setup, not configured
  here — a documented lab-scope simplification). Everything else the app needs — DB proxy
  endpoint, DB port/name, Redis host/port — is plain config, not a secret: it lives in SSM
  Parameter Store under a deterministic `/<EnvironmentName>/...` path.
- **`todo-app/deploy/taskdef.json` is a real, complete, checked-in file — not a template.**
  Every value in it is now static: the image is always `:latest`, both secrets have
  deterministic names (referenced by partial ARN, no random suffix needed), and account/region/
  role names are fixed for this single-environment lab. The workflow doesn't render or generate
  anything — it just zips this file with `appspec.yaml` and uploads it.
- Full HA: RDS Multi-AZ standby + Redis replica. No AUTH token / TLS on Redis (private-subnet +
  security-group isolation only) — a documented lab-scope simplification.
- **Migrations run in a dedicated pipeline stage, not the container's entrypoint.**
  `pipeline.yaml`'s `Migrate` stage (a CodeBuild action, between Source and Deploy) registers the
  incoming `taskdef.json` and runs it once as a standalone `ecs run-task` with the container
  command overridden to `manage.py migrate --noinput`, in the same private subnets/security group
  the real service uses. If that task's exit code isn't 0, the CodeBuild action fails and
  CodeDeploy's blue/green shift never runs — migrations always complete exactly once, before any
  traffic moves. Running `migrate` from the container's own entrypoint instead (the original,
  simpler approach) races: every task a blue/green deploy — or autoscaling right after one —
  starts calls `migrate` on startup concurrently, with nothing to guarantee only one wins on the
  same schema change.
- **ECR lives in its own repo (`todo-bootstrap`), not nested under root.yaml.** It has no
  VPC/network dependency and a completely independent lifecycle from the rest of the
  infrastructure — a bad `ecs.yaml` change rolling back the root stack should never be able to
  touch the image repository holding every previously-built image. Its own dedicated OIDC role
  (`EcrDeployRole`, created in that repo's `bootstrap.yaml` alongside the other two) keeps its
  permissions scoped to exactly that one stack and that one repository.
- **`deploy-infra.yml` has no branchy logic at all, in a script or otherwise.** It used to run an
  if/else block (later a separate script) to resolve `InitialImageUri` — reuse the existing value
  once `EcsStack` exists, otherwise look up the newest ECR tag. The mutable `:latest` tag removed
  the entire problem: there's nothing to resolve, so there's nothing to script. The workflow is
  just checkout → configure credentials → package → deploy → print the ALB endpoint.

## One-time bootstrap (do this before the workflow can run at all)

The IAM role this repo's GitHub Actions workflow assumes, and the S3 bucket `cloudformation
package` stages templates in, are both created by `todo-bootstrap`'s `bootstrap.yaml` — not
anything in this repo. See `todo-bootstrap/README.md` "Deploying bootstrap.yaml" for the full
one-time manual deploy command; it has to run before this repo's workflow can do anything.

## Required GitHub repo configuration (`todo-infra`)

**Secrets** (masked — ARNs/bucket names, per best-practice: identifiers stay in Secrets, never
Variables): `INFRA_DEPLOY_ROLE_ARN`, `ARTIFACT_BUCKET_NAME`, `TEMPLATE_BUCKET_NAME` (all three
come from `todo-bootstrap`'s stack outputs)

**Variables** (plain, non-identifying config — also referenced as `vars.*`, never hardcoded as a
literal in the workflow): `ENVIRONMENT_NAME` (`todo-dev`), `AWS_REGION` (e.g. `us-east-1`),
`ECR_REPOSITORY_NAME` (`todo-app`), `AUTO_DEPLOY_ENABLED` (`true`/`false`)

`AUTO_DEPLOY_ENABLED` gates whether a plain `push` actually deploys anything — `workflow_dispatch`
(manual trigger) always runs regardless. Found the hard way: after intentionally tearing down
`todo-dev-root` to stop billing overnight, an unrelated push to `templates/**` (just moving
`bootstrap.yaml` out to `todo-bootstrap`) silently re-triggered `deploy-infra.yml`, which happily
started recreating the entire stack — the workflow had no concept of "this was torn down on
purpose, stay down." Caught it a few seconds in (only `NetworkStack` had started) and deleted it
again, but the workflow shouldn't have been able to do that unprompted at all. Set this to
`false` whenever intentionally spun down; only flip it to `true` while you actually want every
push to auto-deploy.

## After deploying: nothing to hand off to `todo-app` anymore

`todo-app/deploy/taskdef.json` is fully static (see its README) — every value in it, including
both secret references, is derivable from the fixed account/region/`EnvironmentName` naming
convention, not copied from this stack's outputs. There's nothing left to collect here and paste
into `todo-app`'s GitHub secrets after a deploy.

## Verifying a deploy

```bash
aws cloudformation describe-stacks --stack-name todo-dev-root \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue" --output text
aws cloudformation list-stack-resources --stack-name todo-dev-root  # see the nested child stacks
aws cloudformation describe-stacks --stack-name todo-dev-ecr --query "Stacks[0].Outputs"  # separate stack, separate repo
aws ecs describe-services --cluster todo-dev-cluster --services todo-dev-todo-app
aws ssm get-parameters-by-path --path /todo-dev --output table  # confirm plain config landed
aws codepipeline get-pipeline-state --name todo-dev-todo-app-pipeline  # Source/Migrate/Deploy status
aws codebuild batch-get-builds --ids $(aws codebuild list-builds-for-project \
  --project-name todo-dev-migrate --query "ids[0]" --output text)  # last migrate task's logs/exit
```

Known nested-stack blast-radius tradeoff: because everything but `bootstrap` lives under one
root stack, a failed update to any single child (say, a bad `ecs.yaml` change) rolls back the
whole root stack update, not just that child. This is inherent to real nested stacks — it's the
tradeoff for "one deploy creates/updates everything together," which is what was asked for here.

## Real bugs found and fixed on the first actual deploy to this account

Nine things only surfaced once this was deployed for real and pushed all the way through a live
CI run — none of them caught by `validate-template`:

1. **This AWS account already had a GitHub OIDC provider** (from a prior lab — IAM allows only
   one `token.actions.githubusercontent.com` provider per account). `bootstrap.yaml` no longer
   creates `AWS::IAM::OIDCProvider`; it references the existing provider's deterministic ARN
   (`arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com`) directly.
2. **IAM now requires an OIDC trust policy to include a `sub` or `job_workflow_ref` condition
   that isn't wildcarded to everything**, even when `repository_id`/`repository_owner_id`
   conditions are already present and more durable. Added a `GitHubOwner` parameter and a
   `StringLike` condition on `token.actions.githubusercontent.com:sub` alongside the ID-based
   conditions — and verified via CloudTrail on a rejected `AssumeRoleWithWebIdentity` call that
   this environment's actual `sub` claim isn't the plain GitHub-docs format
   (`repo:{owner}/{repo}:ref:...`) but embeds numeric IDs directly:
   `repo:{owner}@{owner_id}/{repo}@{repo_id}:ref:refs/heads/{branch}`. A condition built from
   the documented format silently never matches and fails closed with a generic
   "Not authorized" error — nothing points you at the actual claim shape except CloudTrail.
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
6. **`CodePipelineServiceRole` was missing `codedeploy:GetApplication`.** Only surfaced once a
   real CodePipeline execution actually reached the Deploy stage — the four other CodeDeploy
   permissions it already had weren't sufficient.
7. **`CodePipelineServiceRole` was also missing `ecs:RegisterTaskDefinition` and `iam:PassRole`
   on the task execution/task role ARNs.** The `CodeDeployToECS` pipeline action registers the
   new task definition revision itself, under the pipeline's own role — not delegated to
   `CodeDeployServiceRole` as might be assumed. Added `TaskExecutionRoleArn`/`TaskRoleArn`
   parameters to `pipeline.yaml` specifically to scope the `PassRole` grant to those two roles.
8. **`InfraDeployRole`/`EcrDeployRole` were missing `cloudformation:GetTemplateSummary` and
   `ssm:*`.** Every earlier deploy in this README used local admin credentials, which mask
   permission gaps in the CI role itself — this only surfaced the first time `deploy-infra.yml`
   actually ran end-to-end under its own role. `aws cloudformation deploy` calls
   `GetTemplateSummary` internally to build the changeset; `ssm:*` is needed because `data.yaml`/
   `cache.yaml` create SSM parameters.
9. **`deploy-infra.yml` recomputed `InitialImageUri` from the newest ECR tag on every run, which
   broke the moment `EcsStack` existed.** Once created, CodeDeploy exclusively owns the running
   task definition — any CloudFormation-driven change to the `Service` (even just re-supplying a
   different `ImageUri` that flows into its `TaskDefinition`) is hard-rejected by ECS:
   `"Unable to update task definition on services with a CODE_DEPLOY deployment controller."`
   Superseded, not just fixed: switching ECR to a mutable `:latest` tag removed the parameter
   (and the recomputation problem) entirely — `ecs.yaml` builds the URI itself, and
   `TaskDefinition`'s `Image` property never changes across a `cloudformation deploy` again,
   regardless of how many times `EcsStack` has already been created. CodeDeploy still exclusively
   owns the *running* task definition after the first deploy, same as before.
