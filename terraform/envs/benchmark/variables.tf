variable "scale_tier" {
  description = "Vector-count scale: \"1m\" | \"10m\" | \"100m\". Drives instance type + EBS sizing per docs/infra.md."
  type        = string

  validation {
    condition     = contains(["1m", "10m", "100m"], var.scale_tier)
    error_message = "scale_tier must be one of: 1m, 10m, 100m."
  }
}

variable "region" {
  description = "AWS region. Single region per D-002."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Single AZ for all instances per D-002."
  type        = string
  default     = "us-east-1a"
}

variable "name_prefix" {
  description = "Prefix on every resource name (e.g. \"vss-bench\")."
  type        = string
  default     = "vss-bench"
}

variable "ssh_ingress_cidrs" {
  description = "Operator CIDR(s) allowed SSH ingress (e.g. [\"203.0.113.4/32\"]). Empty disables SSH entirely."
  type        = list(string)
  default     = []
}

variable "key_name" {
  description = "EC2 key-pair name in the deployment region. Optional."
  type        = string
  default     = null
}

variable "ami_id" {
  description = "Ubuntu 22.04 LTS AMI ID in the deployment region. The data source below resolves the latest by default; override to pin."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags layered on top of the per-deploy defaults."
  type        = map(string)
  default     = {}
}
