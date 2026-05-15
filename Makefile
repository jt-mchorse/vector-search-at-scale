# vector-search-at-scale — operator workflow
#
# `make up SCALE=1m` brings up one VPC + three EC2 backends (pgvector, qdrant, weaviate)
# at the named scale tier. `make down SCALE=1m` tears them all back down. State lives in
# terraform/envs/benchmark/terraform.tfstate (local, single-operator) by default.

ENV := terraform/envs/benchmark
SCALE ?= 1m
TF := terraform -chdir=$(ENV)

.PHONY: help fmt fmt-check validate init plan up down output destroy clean

help:
	@echo "vector-search-at-scale infra workflow"
	@echo ""
	@echo "  make fmt              terraform fmt -recursive"
	@echo "  make fmt-check        terraform fmt -check -recursive (CI gate)"
	@echo "  make validate         terraform init -backend=false + validate per module"
	@echo "  make plan SCALE=1m    terraform plan for the given tier (1m | 10m | 100m)"
	@echo "  make up SCALE=1m      apply the plan"
	@echo "  make down SCALE=1m    destroy everything in the env at the given tier"
	@echo "  make output           show endpoints from current state"
	@echo "  make clean            wipe local terraform state (DOES NOT destroy AWS resources)"

fmt:
	terraform fmt -recursive terraform

fmt-check:
	terraform fmt -check -recursive terraform

# Validate every leaf directory (modules + envs) without contacting AWS.
validate:
	@for dir in terraform/modules/* terraform/envs/* ; do \
	  echo "==> validating $$dir" ; \
	  ( cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate ) || exit 1 ; \
	done

init:
	$(TF) init -input=false

plan: init
	$(TF) plan -var "scale_tier=$(SCALE)"

up: init
	$(TF) apply -auto-approve -var "scale_tier=$(SCALE)"

output:
	$(TF) output

down:
	$(TF) destroy -auto-approve -var "scale_tier=$(SCALE)"

# Doesn't touch AWS — only useful after a successful `make down`.
clean:
	rm -rf $(ENV)/.terraform $(ENV)/.terraform.lock.hcl $(ENV)/terraform.tfstate*
