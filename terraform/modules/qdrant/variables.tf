variable "name" {
  description = "Name prefix applied to every resource (e.g. \"vss-bench-1m-qdrant\")."
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
  description = "SSH security-group ID from the common-network module."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. Sized per scale tier; see docs/infra.md."
  type        = string
}

variable "data_volume_gb" {
  description = "Size of the gp3 data volume backing /qdrant/storage."
  type        = number
}

variable "data_volume_iops" {
  description = "Provisioned IOPS for the gp3 data volume. Default 3000."
  type        = number
  default     = 3000
}

variable "data_volume_throughput_mibps" {
  description = "Provisioned throughput (MiB/s) for the gp3 data volume. Default 125."
  type        = number
  default     = 125
}

variable "ami_id" {
  description = "AMI ID — caller passes Ubuntu 22.04 LTS in the deployment region."
  type        = string
}

variable "key_name" {
  description = "EC2 key-pair name for SSH access. Optional."
  type        = string
  default     = null
}

variable "image_tag" {
  description = "Docker image tag for qdrant. Pinned per D-004."
  type        = string
  default     = "v1.14.0"
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
