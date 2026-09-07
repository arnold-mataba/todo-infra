"""
Diagram-as-code source for the To-Do app architecture diagram.
Regenerate with:
    python3 -m venv .venv && .venv/bin/pip install diagrams
    .venv/bin/python3 architecture.py
Requires Graphviz (`dot`) on PATH.

Follows the standard AWS reference-architecture nesting convention:
AWS Cloud -> Region -> VPC -> Availability Zone -> Subnet, using the official AWS
Architecture Icons shipped with the `diagrams` package (mingrammer/diagrams).
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, ECS, Fargate
from diagrams.aws.database import ElasticacheForRedis, RDSPostgresqlInstance, RDSInstance
from diagrams.aws.devtools import Codedeploy, Codepipeline
from diagrams.aws.integration import Eventbridge
from diagrams.aws.network import ALB, Endpoint, PublicSubnet
from diagrams.aws.security import SecretsManager
from diagrams.aws.general import Users, InternetAlt1

graph_attr = {
    "fontsize": "13",
    "bgcolor": "white",
    "pad": "0.4",
    "splines": "ortho",
}

with Diagram(
    "To-Do App - ECS Fargate + RDS Proxy + ElastiCache",
    filename="architecture",
    outformat="png",
    show=False,
    graph_attr=graph_attr,
    direction="TB",
):
    users = Users("Users")
    internet = InternetAlt1("Internet")

    with Cluster("AWS Cloud"):
        with Cluster("Region: us-east-1"):

            with Cluster("CI/CD (GitHub Actions -> OIDC)"):
                ecr = ECR("ECR\n(todo-app, immutable tags)")
                eventbridge = Eventbridge("EventBridge\nrule: ECR image PUSH")
                pipeline = Codepipeline("CodePipeline\n(S3 source)")
                codedeploy = Codedeploy("CodeDeploy\nECS Blue/Green")

                ecr >> Edge(label="image push event") >> eventbridge
                eventbridge >> Edge(label="StartPipelineExecution") >> pipeline
                pipeline >> codedeploy

            with Cluster("VPC 10.0.0.0/16"):

                with Cluster("Availability Zone A"):
                    with Cluster("Public Subnet A"):
                        pub_a = PublicSubnet("10.0.0.0/24")

                    with Cluster("Private Subnet - ECS - A"):
                        ecs_a = Fargate("ECS Task\n(todo-app)")
                        vpce_a = Endpoint("VPC Endpoints\necr.api / ecr.dkr\nlogs / secretsmanager")

                    with Cluster("Private Subnet - Data - A"):
                        proxy_a = RDSInstance("RDS Proxy")
                        rds_primary = RDSPostgresqlInstance("RDS PostgreSQL\n(primary)")

                    with Cluster("Private Subnet - Cache - A"):
                        redis_primary = ElasticacheForRedis("Redis\n(primary)")

                with Cluster("Availability Zone B"):
                    with Cluster("Public Subnet B"):
                        pub_b = PublicSubnet("10.0.1.0/24")

                    with Cluster("Private Subnet - ECS - B"):
                        ecs_b = Fargate("ECS Task\n(todo-app)")

                    with Cluster("Private Subnet - Data - B"):
                        rds_standby = RDSPostgresqlInstance("RDS PostgreSQL\n(Multi-AZ standby)")

                    with Cluster("Private Subnet - Cache - B"):
                        redis_replica = ElasticacheForRedis("Redis\n(replica)")

                alb = ALB("Application\nLoad Balancer")
                secrets = SecretsManager("DB credentials\n(RDS-managed secret)")

                users >> internet >> alb
                alb >> Edge(label=":8080") >> ecs_a
                alb >> Edge(label=":8080") >> ecs_b

                ecs_a >> Edge(label="writes :5432") >> proxy_a
                ecs_b >> Edge(label="writes :5432") >> proxy_a
                proxy_a >> rds_primary
                rds_primary >> Edge(label="sync replication", style="dashed") >> rds_standby

                ecs_a >> Edge(label="reads :6379") >> redis_primary
                ecs_b >> Edge(label="reads :6379") >> redis_primary
                redis_primary >> Edge(label="replication", style="dashed") >> redis_replica

                ecs_a >> Edge(style="dotted") >> vpce_a
                secrets >> Edge(style="dotted", label="secrets") >> proxy_a
                secrets >> Edge(style="dotted") >> ecs_a

                codedeploy >> Edge(label="blue/green shift", color="firebrick") >> alb
