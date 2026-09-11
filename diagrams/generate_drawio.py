import os
import xml.etree.ElementTree as ET

cells = []
_id = [0]

def nid(prefix):
    _id[0] += 1
    return f"{prefix}-{_id[0]}"

def add_cell(cid, style, x, y, w, h, parent, value="", vertex=1, edge=0, source=None, target=None):
    c = {"id": cid, "style": style, "value": value, "parent": parent, "x": x, "y": y,
         "w": w, "h": h, "vertex": vertex, "edge": edge, "source": source, "target": target}
    cells.append(c)
    return c

class Stack:
    def __init__(self, direction, parent_id=None, pad_top=30, pad_right=25, pad_bottom=25, pad_left=25, gap=35):
        self.direction = direction
        self.parent_id = parent_id
        self.pt, self.pr, self.pb, self.pl = pad_top, pad_right, pad_bottom, pad_left
        self.gap = gap
        self.cursor = 0
        self.cross_max = 0
        self.items = []

    def place(self, w, h):
        if self.direction == "v":
            x, y = self.pl, self.pt + self.cursor
            self.cursor += h + self.gap
            self.cross_max = max(self.cross_max, w)
        else:
            x, y = self.pl + self.cursor, self.pt
            self.cursor += w + self.gap
            self.cross_max = max(self.cross_max, h)
        self.items.append((x, y, w, h))
        return x, y

    def size(self):
        main = self.cursor - self.gap if self.items else 0
        if self.direction == "v":
            return self.pl + self.cross_max + self.pr, self.pt + main + self.pb
        return self.pl + main + self.pr, self.pt + self.cross_max + self.pb

GROUP_BASE = ("points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],"
              "[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];"
              "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=13;"
              "fontStyle=1;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;"
              "shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;")

def group_style(gr_icon, stroke, fill="none", font=None, dashed=0):
    font = font or stroke
    return (GROUP_BASE + f"dashed={dashed};grIcon={gr_icon};strokeColor={stroke};"
            f"fillColor={fill};fontColor={font};")

def icon_style(res_icon, fill):
    return (f"sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
            f"fillColor={fill};strokeColor=none;dashed=0;verticalLabelPosition=bottom;"
            f"verticalAlign=top;align=center;html=1;fontSize=11;fontStyle=0;aspect=fixed;"
            f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res_icon};")

NOTE_STYLE = ("rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF9E8;strokeColor=#D6B656;"
              "fontSize=11;align=left;verticalAlign=top;spacing=8;arcSize=8;")

COMPUTE, DATABASE, NETWORK, SECURITY, INTEGRATION, DEVTOOLS, STORAGE, GENERAL, MGMT = (
    "#ED7100", "#3B48CC", "#8C4FFF", "#DD344C", "#E7157B", "#C925D1", "#7AA116", "#232F3E", "#E7157B")

ICON_W, ICON_H = 56, 56
SLOT_W, SLOT_LABEL_H = 150, 70

def icon(stack, cid, label, res_icon, fill):
    x, y = stack.place(SLOT_W, ICON_H + SLOT_LABEL_H)
    return (cid, label, res_icon, fill, x, y)

def finalize_icon(spec, parent_id):
    cid, label, res_icon, fill, x, y = spec
    add_cell(cid, icon_style(res_icon, fill), x + (SLOT_W - ICON_W) / 2, y, ICON_W, ICON_H, parent_id, label)

def group_spec(label, gr_icon, stroke, fill="none", direction="v", **stack_kw):
    gid = nid("grp")
    inner = Stack(direction, None, **stack_kw)
    return {"id": gid, "inner": inner, "gr_icon": gr_icon, "stroke": stroke, "fill": fill,
            "label": label, "items": []}

def g_icon(gspec, label, res_icon, fill, cid=None):
    cid = cid or nid("i")
    spec = icon(gspec["inner"], cid, label, res_icon, fill)
    gspec["items"].append(("icon", spec))
    return cid

def g_note(gspec, text, w=None, h=90):
    w = w or SLOT_W
    x, y = gspec["inner"].place(w, h)
    cid = nid("note")
    gspec["items"].append(("note", (cid, text, x, y, w, h)))
    return cid

def group_size(gspec):
    return gspec["inner"].size()

def finalize_group(gspec, x, y, parent_id):
    w, h = group_size(gspec)
    add_cell(gspec["id"], group_style(gspec["gr_icon"], gspec["stroke"], gspec["fill"]), x, y, w, h, parent_id, gspec["label"])
    for kind, item in gspec["items"]:
        if kind == "icon":
            finalize_icon(item, gspec["id"])
        else:
            cid, text, ix, iy, iw, ih = item
            add_cell(cid, NOTE_STYLE, ix, iy, iw, ih, gspec["id"], text)
    return gspec["id"]

def hgrid(specs_with_sizes, gap=60):
    row = Stack("h", None, pad_top=0, pad_right=0, pad_bottom=0, pad_left=0, gap=gap)
    positions = [row.place(w, h) for (w, h) in specs_with_sizes]
    return row.size(), positions

def subnet_spec(label, kind, icon_specs):
    gid = nid("subnet")
    inner = Stack("v", None, pad_top=45, pad_right=20, pad_bottom=20, pad_left=20, gap=25)
    placed = []
    for spec in icon_specs:
        x, y = inner.place(SLOT_W, ICON_H + SLOT_LABEL_H)
        placed.append((x, y, spec))
    w, h = inner.size()
    stroke, fill = ("#7AA116", "#E9F3E6") if kind == "public" else ("#00A4A6", "#E6F2F8")
    return {"id": gid, "w": w, "h": h, "label": label, "stroke": stroke, "fill": fill, "placed": placed}

def finalize_subnet(spec, x, y, parent_id):
    add_cell(spec["id"], group_style("mxgraph.aws4.group_security_group", spec["stroke"], spec["fill"]),
              x, y, spec["w"], spec["h"], parent_id, spec["label"])
    for (cx, cy, (cid, ic_label, res_icon, fill_c)) in spec["placed"]:
        add_cell(cid, icon_style(res_icon, fill_c), cx + (SLOT_W - ICON_W) / 2, cy, ICON_W, ICON_H, spec["id"], ic_label)

root_id = "1"
cloud_id = nid("cloud")
region_id = nid("region")

boot = group_spec("Bootstrap  (bootstrap.yaml — one-time manual deploy)", "mxgraph.aws4.group_generic", "#545B64",
                   pad_top=45, pad_right=25, pad_bottom=25, pad_left=25, gap=30)
g_icon(boot, "GitHub OIDC Identity Provider\ntoken.actions.githubusercontent.com", "identity_and_access_management_iam_role", SECURITY)
g_icon(boot, "InfraDeployRole\ntodo-dev-infra-deploy-role\ntrust: repo=todo-infra", "identity_and_access_management_iam_role", SECURITY)
g_icon(boot, "AppBuildRole\ntodo-dev-app-build-role\ntrust: repo=todo-app", "identity_and_access_management_iam_role", SECURITY)
g_icon(boot, "Template Bucket (S3)\ncfn-templates staging\nfor `cfn package`", "simple_storage_service_bucket", STORAGE)

ecr_g = group_spec("ECR Repository  (ecr.yaml — its own nested stack)", "mxgraph.aws4.group_generic", COMPUTE,
                    pad_top=45, pad_right=25, pad_bottom=25, pad_left=25, gap=30)
ecr_icon_id = g_icon(ecr_g, "todo-app\nImageTagMutability: IMMUTABLE\nScanOnPush: true", "elastic_container_registry", COMPUTE)
g_note(ecr_g, "Lifecycle policy: expire, keep\nlast 5 images (imageCountMoreThan)", h=70)

pipe = group_spec("CI/CD Pipeline  (pipeline.yaml)", "mxgraph.aws4.group_generic", INTEGRATION,
                   pad_top=45, pad_right=25, pad_bottom=25, pad_left=25, gap=30)
eb_icon_id = g_icon(pipe, "EventBridge Rule\nsource=aws.ecr, action=PUSH\nrepository-name=todo-app", "eventbridge", INTEGRATION)
artifactbucket_id = g_icon(pipe, "Artifact Bucket (S3)\ndeploy-artifacts/artifacts.zip\nversioned, private", "simple_storage_service_bucket", STORAGE)
cp_icon_id = g_icon(pipe, "CodePipeline\nSource: S3 only (no ECR\nsource action — immutable tags)", "codepipeline", DEVTOOLS)
cd_icon_id = g_icon(pipe, "CodeDeploy\nApp + DeploymentGroup\nBLUE_GREEN, ECSAllAtOnce", "codedeploy", DEVTOOLS)

cfg = group_spec("Configuration & Secrets  (regional services)", "mxgraph.aws4.group_generic", SECURITY,
                  pad_top=45, pad_right=25, pad_bottom=25, pad_left=25, gap=30)
dbsecret_id = g_icon(cfg, "DB Credentials\nRDS-managed secret\n(auto-rotated name)", "secrets_manager", SECURITY)
djsecret_id = g_icon(cfg, "Django SECRET_KEY\ntodo-dev-django-secret-key\n(generated, 50 chars)", "secrets_manager", SECURITY)
ssm_id = g_icon(cfg, "SSM Parameters (free tier)\n/todo-dev/db-proxy-endpoint\n/todo-dev/db-port, db-name\n/todo-dev/redis-host, redis-port", "systems_manager", MGMT)

(row1_w, row1_h), row1_pos = hgrid([group_size(boot), group_size(ecr_g)])
(row2_w, row2_h), row2_pos = hgrid([group_size(pipe), group_size(cfg)])
left_inner_w = max(row1_w, row2_w)

left_col_local = Stack("v", None, pad_top=50, pad_right=30, pad_bottom=30, pad_left=30, gap=50)
lr1x, lr1y = left_col_local.place(row1_w, row1_h)
lr2x, lr2y = left_col_local.place(row2_w, row2_h)
left_w, left_h = left_col_local.size()
left_col_id = nid("leftcol")

top_row = Stack("h", None, pad_top=0, pad_right=0, pad_bottom=0, pad_left=0, gap=90)
alb_spec_pos = top_row.place(SLOT_W, ICON_H + SLOT_LABEL_H)
note_pos = top_row.place(360, ICON_H + SLOT_LABEL_H)
tw, th = top_row.size()

def build_az(is_a):
    letter = "A" if is_a else "B"
    pub = subnet_spec(f"Public Subnet {letter}  (10.0.{0 if is_a else 1}.0/24)", "public", [])

    ecs_task_id = nid(f"ecs{letter}")
    vpce_id = nid(f"vpce{letter}")
    ecs_icons = [
        (ecs_task_id, "ECS Task (Fargate)\ntodo-app container :8080\nExecRole + TaskRole (IAM)", "fargate", COMPUTE),
        (vpce_id, "VPC Interface Endpoints\necr.api, ecr.dkr, logs,\nsecretsmanager, ssm (443)", "endpoints", NETWORK),
    ]
    ecs = subnet_spec(f"Private Subnet - ECS - {letter}  (sg: todo-dev-ecs-sg)", "private", ecs_icons)

    if is_a:
        proxy_id = nid("proxy")
        rds_id = nid("rdsA")
        data_icons = [
            (proxy_id, "RDS Proxy\nrds-proxy-role (IAM)\nAuth: Secrets Manager", "rds_instance", DATABASE),
            (rds_id, "RDS PostgreSQL (primary)\ndb.t3.micro, Multi-AZ\nStorage: 20GB gp3, encrypted", "rds_instance", DATABASE),
        ]
    else:
        proxy_id = None
        rds_id = nid("rdsB")
        data_icons = [(rds_id, "RDS PostgreSQL\n(Multi-AZ standby, sync\nreplica of primary in AZ-A)", "rds_instance", DATABASE)]
    data = subnet_spec(f"Private Subnet - Data - {letter}  (sg: rds-proxy-sg / rds-sg)", "private", data_icons)

    redis_id = nid(f"redis{letter}")
    cache_icons = [(redis_id,
                    "Redis (primary)\ncache.t3.micro\nAutomaticFailover: true" if is_a else
                    "Redis (replica)\ncache.t3.micro\nsync replication from A",
                    "elasticache_for_redis", DATABASE)]
    cache = subnet_spec(f"Private Subnet - Cache - {letter}  (sg: todo-dev-redis-sg)", "private", cache_icons)

    (r1w, r1h), r1pos = hgrid([(pub["w"], pub["h"]), (ecs["w"], ecs["h"])], gap=40)
    (r2w, r2h), r2pos = hgrid([(data["w"], data["h"]), (cache["w"], cache["h"])], gap=40)

    az_stack = Stack("v", None, pad_top=50, pad_right=30, pad_bottom=30, pad_left=30, gap=40)
    ar1x, ar1y = az_stack.place(r1w, r1h)
    ar2x, ar2y = az_stack.place(r2w, r2h)
    aw, ah = az_stack.size()
    az_id = nid("az")

    finalize_subnet(pub, ar1x + r1pos[0][0], ar1y + r1pos[0][1], az_id)
    finalize_subnet(ecs, ar1x + r1pos[1][0], ar1y + r1pos[1][1], az_id)
    finalize_subnet(data, ar2x + r2pos[0][0], ar2y + r2pos[0][1], az_id)
    finalize_subnet(cache, ar2x + r2pos[1][0], ar2y + r2pos[1][1], az_id)

    ids = {"ecs": ecs_task_id, "vpce": vpce_id, "data": proxy_id or rds_id, "rds": rds_id, "cache": redis_id}
    return az_id, aw, ah, ids

az_a_id, az_a_w, az_a_h, az_a_ids = build_az(True)
az_b_id, az_b_w, az_b_h, az_b_ids = build_az(False)
(az_row_w, az_row_h), az_row_pos = hgrid([(az_a_w, az_a_h), (az_b_w, az_b_h)], gap=90)

vpc_inner = Stack("v", None, pad_top=55, pad_right=45, pad_bottom=45, pad_left=45, gap=55)
tx, ty = vpc_inner.place(tw, th)
rx, ry = vpc_inner.place(az_row_w, az_row_h)
vpc_w, vpc_h = vpc_inner.size()
vpc_id = nid("vpc")

alb_id = nid("alb")
add_cell(alb_id, icon_style("application_load_balancer", NETWORK),
         alb_spec_pos[0] + tx + (SLOT_W - ICON_W) / 2, alb_spec_pos[1] + ty, ICON_W, ICON_H, vpc_id,
         "Application Load Balancer\nListener :80 -> TG blue/green\nHealth check: /health/")
add_cell(nid("svcnote"), NOTE_STYLE, note_pos[0] + tx, note_pos[1] + ty, 360, ICON_H + SLOT_LABEL_H, vpc_id,
         "ECS Service: todo-dev-todo-app\nAuto Scaling: min 1 / desired 1 / max 4\nTarget tracking: 60% avg CPU\nDeploymentController: CODE_DEPLOY\nTask size: 0.5 vCPU / 1 GB (Fargate)")

add_cell(az_a_id, group_style("mxgraph.aws4.group_availability_zone", "#147EBA", dashed=1),
         rx + az_row_pos[0][0], ry + az_row_pos[0][1], az_a_w, az_a_h, vpc_id, "Availability Zone A")
add_cell(az_b_id, group_style("mxgraph.aws4.group_availability_zone", "#147EBA", dashed=1),
         rx + az_row_pos[1][0], ry + az_row_pos[1][1], az_b_w, az_b_h, vpc_id, "Availability Zone B")

(region_row_w, region_row_h), region_row_pos = hgrid([(left_w, left_h), (vpc_w, vpc_h)], gap=70)
region_stack = Stack("v", None, pad_top=70, pad_right=50, pad_bottom=50, pad_left=50, gap=0)
region_x, region_y = region_stack.place(region_row_w, region_row_h)
region_w, region_h = region_stack.size()

add_cell(left_col_id, group_style("mxgraph.aws4.group_generic", "#232F3E"),
         region_x + region_row_pos[0][0], region_y + region_row_pos[0][1], left_w, left_h, region_id,
         "Platform Services  (region-level, outside VPC)")
finalize_group(boot, lr1x + row1_pos[0][0], lr1y + row1_pos[0][1], left_col_id)
finalize_group(ecr_g, lr1x + row1_pos[1][0], lr1y + row1_pos[1][1], left_col_id)
finalize_group(pipe, lr2x + row2_pos[0][0], lr2y + row2_pos[0][1], left_col_id)
finalize_group(cfg, lr2x + row2_pos[1][0], lr2y + row2_pos[1][1], left_col_id)

add_cell(vpc_id, group_style("mxgraph.aws4.group_vpc2", "#248814"),
         region_x + region_row_pos[1][0], region_y + region_row_pos[1][1], vpc_w, vpc_h, region_id,
         "VPC 10.0.0.0/16  (2 AZs, no NAT Gateway)")

add_cell(region_id, group_style("mxgraph.aws4.group_region", "#147EBA", dashed=1), 60, 60, region_w, region_h, cloud_id, "Region: us-east-1")

cloud_w, cloud_h = region_w + 120, region_h + 120
CLOUD_X = 340
add_cell(cloud_id, group_style("mxgraph.aws4.group_aws_cloud_alt", "#232F3E"), CLOUD_X, 60, cloud_w, cloud_h, root_id, "AWS Cloud")

users_id = nid("users")
internet_id = nid("internet")
add_cell(users_id, icon_style("users", GENERAL), 60, 60 + cloud_h / 2 - 130, ICON_W, ICON_H, root_id, "Users")
add_cell(internet_id, icon_style("internet_alt1", NETWORK), 60, 60 + cloud_h / 2 - 10, ICON_W, ICON_H, root_id, "Internet")

EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;fontSize=10;endArrow=block;elbow=vertical;strokeWidth=1.5;"

def edge(src, tgt, label="", style_extra=""):
    add_cell(nid("edge"), EDGE + style_extra, 0, 0, 0, 0, root_id, label, vertex=0, edge=1, source=src, target=tgt)

edge(users_id, internet_id)
edge(internet_id, alb_id)
edge(alb_id, az_a_ids["ecs"], ":8080")
edge(alb_id, az_b_ids["ecs"], ":8080")

edge(az_a_ids["ecs"], az_a_ids["data"], "writes :5432")
edge(az_b_ids["ecs"], az_a_ids["data"], "writes :5432")
edge(az_a_ids["data"], az_a_ids["rds"])
edge(az_a_ids["rds"], az_b_ids["rds"], "sync replication", "dashed=1;strokeColor=#3B48CC;")

edge(az_a_ids["ecs"], az_a_ids["cache"], "reads :6379")
edge(az_b_ids["ecs"], az_a_ids["cache"], "reads :6379")
edge(az_a_ids["cache"], az_b_ids["cache"], "replication", "dashed=1;strokeColor=#3B48CC;")

edge(az_a_ids["ecs"], az_a_ids["vpce"], "", "dashed=1;strokeColor=#8C4FFF;")
edge(az_b_ids["ecs"], az_b_ids["vpce"], "", "dashed=1;strokeColor=#8C4FFF;")

edge(dbsecret_id, az_a_ids["data"], "secrets:GetSecretValue", "dashed=1;strokeColor=#DD344C;")
edge(dbsecret_id, az_a_ids["ecs"], "", "dashed=1;strokeColor=#DD344C;")
edge(djsecret_id, az_a_ids["ecs"], "", "dashed=1;strokeColor=#DD344C;")
edge(ssm_id, az_a_ids["ecs"], "ssm:GetParameters", "dashed=1;strokeColor=#DD344C;")
edge(ssm_id, az_b_ids["ecs"], "", "dashed=1;strokeColor=#DD344C;")

edge(ecr_icon_id, eb_icon_id, "image PUSH event")
edge(eb_icon_id, cp_icon_id, "StartPipelineExecution")
edge(artifactbucket_id, cp_icon_id, "S3 source\n(taskdef.json + appspec.yaml)")
edge(cp_icon_id, cd_icon_id)
edge(cd_icon_id, alb_id, "blue/green traffic shift", "strokeColor=#B20000;strokeWidth=2;")

mxfile = ET.Element("mxfile", host="app.diagrams.net")
diagram = ET.SubElement(mxfile, "diagram", id="todo-app-arch", name="Network Architecture")
graph = ET.SubElement(diagram, "mxGraphModel", dx="1600", dy="900", grid="0", gridSize="10",
                       guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1",
                       pageScale="1", pageWidth="2400", pageHeight="1900", math="0", shadow="0")
root = ET.SubElement(graph, "root")
ET.SubElement(root, "mxCell", id="0")
ET.SubElement(root, "mxCell", id="1", parent="0")

for c in cells:
    attrs = {"id": c["id"], "value": c["value"], "style": c["style"], "vertex": str(c["vertex"]), "parent": c["parent"]}
    if c["edge"]:
        attrs["edge"] = "1"
        attrs["source"] = c["source"]
        attrs["target"] = c["target"]
        del attrs["vertex"]
    cell = ET.SubElement(root, "mxCell", attrs)
    geom = ET.SubElement(cell, "mxGeometry", x=str(c["x"]), y=str(c["y"]), width=str(c["w"]), height=str(c["h"]))
    geom.set("as", "geometry")
    if c["edge"]:
        geom.set("relative", "1")

xml_str = ET.tostring(mxfile, encoding="unicode")
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "architecture.drawio")
with open(out_path, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write(xml_str)

print("wrote", len(cells), "cells")
print("cloud size", cloud_w, cloud_h)
