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

- id: D-005
  date: 2026-05-15
  decision: backend_is_protocol_with_ingest_and_query_lazy_sdk_imports_per_extra
  rationale: same_swappable_seam_pattern_as_eval_harness_backend_and_rag_kit_embedder_reranker_generator
  alternatives_rejected: [abstract_base_class, single_concrete_class_with_branching, langchain_style_chain]
  reversibility: cheap
  related_issues: [#2]
  superseded_by: null

- id: D-006
  date: 2026-05-15
  decision: stub_backend_ships_in_base_install_for_hermetic_ci_recall_one_by_construction
  rationale: ci_exercises_full_harness_without_aws_bring_up_real_engines_score_against_the_stub
  alternatives_rejected: [require_aws_for_any_test, mock_engine_clients_in_each_test_file]
  reversibility: cheap
  related_issues: [#2]
  superseded_by: null

- id: D-007
  date: 2026-05-15
  decision: one_json_file_per_run_id_under_results_refuse_overwrite_without_force
  rationale: idempotency_by_filesystem_not_replay_clear_failure_mode_when_operator_typos_run_id_collision
  alternatives_rejected: [sqlite_history_overkill_for_this_scale, append_only_jsonl_no_per_run_atomicity, no_persistence]
  reversibility: cheap
  related_issues: [#2, #3, #4, #5]
  superseded_by: null
