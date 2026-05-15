# Core Decisions (AI-readable, YAML, append-only)
# Schema: see .skills/portfolio-memory/SKILL.md

- id: D-001
  date: 2026-05-10
  decision: scope_per_portfolio_handoff_section_2
  rationale: locked_scope_prevents_drift
  alternatives_rejected: []
  reversibility: expensive
  related_issues: []
  superseded_by: null

- id: D-002
  date: 2026-05-15
  decision: aws_single_region_single_az_for_benchmark_substrate
  rationale: cross_az_latency_would_muddy_engine_comparison_single_region_keeps_egress_costs_zero
  alternatives_rejected: [multi_az_for_realism, gcp, multi_cloud]
  reversibility: cheap
  related_issues: [#1]
  superseded_by: null

- id: D-003
  date: 2026-05-15
  decision: third_backend_is_weaviate_oss_self_hosted
  rationale: pgvector_qdrant_already_self_hosted_pinecone_saas_only_would_compare_cloud_infra_not_engines
  alternatives_rejected: [pinecone, milvus, vespa]
  reversibility: cheap
  related_issues: [#1]
  superseded_by: null

- id: D-004
  date: 2026-05-15
  decision: single_node_per_backend_no_replication_pinned_docker_tags
  rationale: reproducibility_is_the_load_bearing_constraint_for_benchmarks
  alternatives_rejected: [hand_managed_install, latest_floating_tags, replicated_clusters]
  reversibility: cheap
  related_issues: [#1]
  superseded_by: null
