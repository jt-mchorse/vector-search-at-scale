variable "name" {
  description = "Name prefix applied to every resource (e.g. \"vss-bench-1m-pgvector\")."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID from the common-network module."
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID from the common-network module."
  type        = string
}

variable "ssh_security_group_id" {
  description = "SSH security-group ID from the common-network module. Attached so the operator can shell in if needed."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. Sized per scale tier; see docs/infra.md for the table."
  type        = string
}

variable "data_volume_gb" {
  description = "Size of the gp3 data volume backing /var/lib/postgresql. Sized per scale tier; see docs/infra.md."
  type        = number
}

variable "data_volume_iops" {
  description = "Provisioned IOPS for the gp3 data volume. Default 3000 (gp3 baseline)."
  type        = number
  default     = 3000
}

variable "data_volume_throughput_mibps" {
  description = "Provisioned throughput (MiB/s) for the gp3 data volume. Default 125 (gp3 baseline)."
  type        = number
  default     = 125
}

variable "ami_id" {
  description = "AMI ID for the EC2 instance. Caller should pass an Ubuntu 22.04 LTS AMI in the deployment region."
  type        = string
}

variable "key_name" {
  description = "EC2 key-pair name for SSH access. Optional; leave null to skip."
  type        = string
  default     = null
}

variable "image_tag" {
  description = "Docker image tag for pgvector. Pinned per D-004 so reruns are reproducible."
  type        = string
  default     = "pg16"
}

variable "postgres_password" {
  description = "Postgres superuser password used inside the benchmark instance. Not sensitive in benchmark context (single-tenant ephemeral instance) but plumbed as sensitive anyway."
  type        = string
  default     = "benchmark"
  sensitive   = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
