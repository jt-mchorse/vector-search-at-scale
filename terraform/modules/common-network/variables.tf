variable "name" {
  description = "Name prefix applied to every resource in this module."
  type        = string
}

variable "region" {
  description = "AWS region for the benchmark deployment. Single region by D-002."
  type        = string
}

variable "availability_zone" {
  description = "Single AZ for all instances. Single-AZ on purpose (D-002) so latency numbers aren't muddied by cross-AZ hops."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR for the benchmark VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR for the single public subnet that hosts the benchmark EC2 instances."
  type        = string
  default     = "10.42.1.0/24"
}

variable "ssh_ingress_cidrs" {
  description = "CIDRs allowed to SSH (port 22) into the benchmark instances. Default is empty — set explicitly to your /32 before applying."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
