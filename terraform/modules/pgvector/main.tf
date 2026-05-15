resource "aws_security_group" "service" {
  name        = "${var.name}-service"
  description = "pgvector port (5432) ingress, restricted to inside-VPC."
  vpc_id      = var.vpc_id

  ingress {
    description = "Postgres from inside the VPC only (benchmark client runs in the same subnet)."
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.42.0.0/16"]
  }

  egress {
    description = "Allow all egress (Docker pulls, package installs, telemetry)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name}-service"
  })
}

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.this.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  iops              = var.data_volume_iops
  throughput        = var.data_volume_throughput_mibps

  tags = merge(var.tags, {
    Name = "${var.name}-data"
  })
}

data "aws_subnet" "this" {
  id = var.subnet_id
}

resource "aws_instance" "this" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.service.id, var.ssh_security_group_id]
  associate_public_ip_address = true
  key_name                    = var.key_name

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    image_tag         = var.image_tag
    postgres_password = var.postgres_password
  })

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = merge(var.tags, {
    Name    = var.name
    Backend = "pgvector"
  })
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.this.id

  # On termination, detach without leaving an attachment lock that blocks `terraform destroy`.
  force_detach = true
}
