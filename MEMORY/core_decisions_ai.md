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

- id: D-008
  date: 2026-05-16
  decision: latency_under_load_driven_by_threadpoolexecutor_over_backend_protocol_not_k6_locust
  rationale: k6_is_http_only_but_pgvector_speaks_postgres_wire_protocol_threadpoolexecutor_over_backend_keeps_apples_to_apples_across_all_three_backends_with_zero_translation_layer
  alternatives_rejected: [k6_with_http_shim_for_pgvector, locust_with_two_separate_drivers_per_protocol_family, asyncio_based_driver_when_sdks_are_sync]
  reversibility: cheap
  related_issues: [#4]
  superseded_by: null

- id: D-009
  date: 2026-05-17
  decision: hnsw_sim_backend_is_pure_numpy_simulation_of_recall_latency_tradeoff_not_a_real_hnsw_implementation
  rationale: parameter_grid_study_runs_in_hermetic_ci_without_qdrant_weaviate_pgvector_bring_up_same_scripts_apply_to_real_backends_via_backend_arg_simulation_produces_qualitatively_correct_curve_shape_real_engines_diverge_in_absolute_numbers_but_shape_is_what_tuning_study_cares_about
  alternatives_rejected: [require_aws_for_any_grid_run_breaks_dep_free_default, vendor_hnswlib_as_required_dep_adds_a_c_extension_for_a_demo, leave_to_operator_with_no_baseline_curve_makes_grid_script_untestable]
  reversibility: cheap
  related_issues: [#3]
  superseded_by: null

- id: D-010
  date: 2026-05-17
  decision: cost_model_ships_documented_aws_us_east_1_list_price_snapshot_with_caller_override_via_pricetable
  rationale: snapshot_dated_and_url_d_so_table_is_reviewable_caller_override_lets_operators_with_reserved_spot_edp_or_non_us_east_1_compute_same_table_without_editing_repo_state_unknown_instance_types_raise_so_table_never_silently_uses_fabricated_price_mirrors_llm_cost_optimizer_d_003_posture
  alternatives_rejected: [ship_no_defaults_table_cant_be_regenerated_by_ci_operator_wires_prices_every_run, build_pricing_into_model_code_snapshot_date_and_source_url_lose_visibility, online_price_lookup_against_aws_api_ci_dns_flakes_and_rates_move_slowly_enough_that_offline_snapshot_is_honest]
  reversibility: cheap
  related_issues: [5]
  superseded_by: null

- id: D-011
  date: 2026-05-22
  decision: run_benchmark_refuses_concurrency_gt_1_use_run_under_load
  rationale: run_benchmark_executed_queries_serially_but_recorded_workload_concurrency_field_verbatim_so_a_misconfigured_caller_got_a_results_json_whose_latency_stats_disagreed_with_recorded_concurrency_silent_numerical_quality_bug_load_module_already_exists_as_well_shaped_concurrent_entry_point
  alternatives_rejected: [make_run_benchmark_parallelize_silently_duplicates_load_module_logic, remove_workload_concurrency_field_breaks_load_module_which_uses_it_per_cell, document_only_no_runtime_enforcement_same_drift_pattern_chunking_lab_d_011_just_closed]
  reversibility: cheap
  related_issues: [19]
  superseded_by: null

- id: D-012
  date: 2026-05-26
  decision: atomic_write_helper_lives_in_package_level_vector_bench_io_utils_module_following_portfolio_standard
  rationale: matches_rag_kit_eval_harness_emb_shootout_async_pipelines_chunking_lab_centralizes_test_surface_one_os_replace_to_monkey_patch_for_five_call_sites
  alternatives_rejected: [file_private_per_module, inline_pattern_at_each_call_site]
  reversibility: cheap
  related_issues: [#33]
  superseded_by: null
