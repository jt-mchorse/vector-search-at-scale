provider "aws" {
  region = var.region

  default_tags {
    tags = merge({
      Project   = "vector-search-at-scale"
      ManagedBy = "terraform"
      ScaleTier = var.scale_tier
      Ephemeral = "true"
    }, var.tags)
  }
}

# Per-tier sizing. Numbers are derived in docs/infra.md from raw vector + HNSW
# memory budget. Adjust there if reality disagrees, then re-document.
locals {
  tiers = {
    "1m" = {
      instance_type    = "m6i.large" # 2 vCPU, 8 GiB
      data_volume_gb   = 50
      iops             = 3000
      throughput_mibps = 125
    }
    "10m" = {
      instance_type    = "r6i.xlarge" # 4 vCPU, 32 GiB
      data_volume_gb   = 200
      iops             = 6000
      throughput_mibps = 250
    }
    "100m" = {
      instance_type    = "r6i.4xlarge" # 16 vCPU, 128 GiB
      data_volume_gb   = 1500
      iops             = 12000
      throughput_mibps = 500
    }
  }

  tier            = local.tiers[var.scale_tier]
  resolved_ami_id = var.ami_id != null ? var.ami_id : data.aws_ami.ubuntu.id
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

module "network" {
  source            = "../../modules/common-network"
  name              = "${var.name_prefix}-${var.scale_tier}"
  region            = var.region
  availability_zone = var.availability_zone
  ssh_ingress_cidrs = var.ssh_ingress_cidrs
}

module "pgvector" {
  source                       = "../../modules/pgvector"
  name                         = "${var.name_prefix}-${var.scale_tier}-pgvector"
  vpc_id                       = module.network.vpc_id
  subnet_id                    = module.network.subnet_id
  ssh_security_group_id        = module.network.ssh_security_group_id
  instance_type                = local.tier.instance_type
  data_volume_gb               = local.tier.data_volume_gb
  data_volume_iops             = local.tier.iops
  data_volume_throughput_mibps = local.tier.throughput_mibps
  ami_id                       = local.resolved_ami_id
  key_name                     = var.key_name
}

module "qdrant" {
  source                       = "../../modules/qdrant"
  name                         = "${var.name_prefix}-${var.scale_tier}-qdrant"
  vpc_id                       = module.network.vpc_id
  subnet_id                    = module.network.subnet_id
  ssh_security_group_id        = module.network.ssh_security_group_id
  instance_type                = local.tier.instance_type
  data_volume_gb               = local.tier.data_volume_gb
  data_volume_iops             = local.tier.iops
  data_volume_throughput_mibps = local.tier.throughput_mibps
  ami_id                       = local.resolved_ami_id
  key_name                     = var.key_name
}

module "weaviate" {
  source                       = "../../modules/weaviate"
  name                         = "${var.name_prefix}-${var.scale_tier}-weaviate"
  vpc_id                       = module.network.vpc_id
  subnet_id                    = module.network.subnet_id
  ssh_security_group_id        = module.network.ssh_security_group_id
  instance_type                = local.tier.instance_type
  data_volume_gb               = local.tier.data_volume_gb
  data_volume_iops             = local.tier.iops
  data_volume_throughput_mibps = local.tier.throughput_mibps
  ami_id                       = local.resolved_ami_id
  key_name                     = var.key_name
}
