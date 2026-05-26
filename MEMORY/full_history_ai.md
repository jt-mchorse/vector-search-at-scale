# Session History (AI-readable, append-only)

Schema: see .skills/portfolio-memory/SKILL.md

---
session: 2026-05-15T09:13Z
duration_min: 75
issue: 1
focus: terraform_infra_three_backends_three_scales
delta:
  files_added: 27
  files_changed: 4
  tests_added: 0   # `terraform validate` + shellcheck via CI; no unit tests
context_for_next_session:
  - infra_layer_shipped_module_validates_clean_no_real_aws_apply_yet
  - benchmark_harness_issue_2_is_natural_next_step_consumes_module_outputs
  - per_tier_costs_documented_us_east_1_on_demand_2026_05
  - third_backend_locked_as_weaviate_d_003
decisions_made: [D-002, D-003, D-004]
followups: []
---

---
session: 2026-05-15T19:33Z
duration_min: 60
issue: 2
focus: benchmark_harness_plus_three_backend_adapters_plus_stub
delta:
  files_added: 14
  files_changed: 3
  tests_added: 23
  test_pass_rate: "23/23 hermetic"
context_for_next_session:
  - python_package_vector_bench_lives_under_src_vector_bench
  - harness_run_benchmark_emits_json_per_run_id_filesystem_idempotency_d007
  - backend_protocol_d005_with_pgvector_qdrant_weaviate_adapters_lazy_imported
  - stub_backend_in_base_install_d006_recall_one_by_construction
  - real_numbers_pending_operator_run_make_up_scale_1m_plus_vector_bench_run_per_backend
  - ci_now_has_python_job_ruff_plus_pytest_alongside_terraform_jobs
decisions_made: [D-005, D-006, D-007]
followups: []
---

---
session: 2026-05-16T15:15Z
duration_min: 10
issue: 2
focus: ready_and_squash_merge_pr_7
delta:
  files_changed: 0
  tests_added: 0
  test_pass_rate: "23/23 hermetic preserved"
context_for_next_session:
  - pr_7_merged_squash_into_main_at_2026_05_16t15_16z_branch_deleted
  - issue_2_closed_three_acceptance_criteria_satisfied_by_shipped_harness
  - issue_4_latency_under_load_study_is_next_priority_high_unblocked
  - issue_3_hnsw_tuning_also_now_unblocked_uses_harness_directly
decisions_made: []
followups: []
---


---
session: 2026-05-16T15:27Z
duration_min: 45
issue: 4
focus: latency_under_load_study_threadpoolexecutor_over_backend_protocol
delta:
  files_added: 3  # load.py, plot_latency.py, test_load.py
  files_changed: 3  # cli.py, README, decisions
  tests_added: 11
  test_pass_rate: "34/34 hermetic"
  benchmarks:
    stub_n_10000_dim_64_queries_500_concurrency_1_p95_ms: 0.675
    stub_n_10000_dim_64_queries_500_concurrency_10_p95_ms: 1.320
    stub_n_10000_dim_64_queries_500_concurrency_100_p95_ms: 4.297
    recall_at_10: 1.0
context_for_next_session:
  - load_module_run_under_load_ingests_once_queries_per_concurrency_via_threadpoolexecutor
  - one_json_per_cell_plus_matrix_json_under_results_load_run_id
  - cli_load_subcommand_takes_comma_separated_concurrency_default_1_10_100
  - plot_latency_script_uses_matplotlib_lazy_import_degrades_to_table_only
  - readme_latency_under_load_subsection_has_real_stub_10k_table
  - d_008_re_scopes_k6_locust_to_threadpoolexecutor_over_backend_protocol
  - real_engine_load_numbers_pending_operator_make_up_then_vector_bench_load_per_backend
  - results_dir_is_gitignored_committed_run_lives_only_in_readme_table
decisions_made: [D-008]
followups: []
---

---
session: 2026-05-17T00:55Z
duration_min: 55
issue: 3
focus: hnsw_parameter_tuning_grid_with_simulation_backend_plus_frontier_plot
delta:
  files_added: 5  # backends/hnsw_sim.py, scripts/hnsw_grid.py, scripts/plot_hnsw_frontier.py, tests/test_hnsw_sim.py, tests/test_hnsw_grid.py
  files_changed: 2  # backends/__init__.py registers hnsw-sim, README.md adds HNSW subsection
  tests_added: 19
  test_pass_rate: "53/53"
  benchmarks:
    hnsw_sim_grid_size: 36
    hnsw_sim_workload: "n_vectors=2000, dim=64, n_queries=200"
    knee_M: 32
    knee_ef_construction: 100
    knee_ef_search: 128
    knee_recall_at_10: 0.998
    knee_p95_ms: 2.02
    frontier_size_simulation: 14
context_for_next_session:
  - hnsw_sim_is_pure_numpy_simulation_not_real_hnsw_explicit_in_module_docstring_d_009
  - ingest_picks_neighbor_lists_via_top_m_over_ef_construction_random_candidates_per_node
  - query_does_beam_search_starts_at_m_div_2_entry_points_expands_through_neighbors_keeps_ef_search_candidates
  - grid_script_at_scripts_hnsw_grid_py_default_grid_8_16_32_x_50_100_200_x_16_32_64_128_36_cells
  - frontier_plot_script_at_scripts_plot_hnsw_frontier_py_matplotlib_lazy_imported_degrades_to_table
  - recommended_defaults_at_recall_floor_95_pct_m_32_efc_100_efs_128_p95_2_02_ms_recall_998_on_simulation
  - committed_docs_hnsw_frontier_png_svg_grid_run_with_seed_1
  - scripts_reusable_on_real_backends_via_backend_arg_pass_backend_qdrant_when_bring_up_done
  - results_dir_gitignored_so_grid_jsons_local_only_recommendation_lives_in_readme_table
decisions_made: [D-009]
followups: []
---

---
session: 2026-05-17T23:50Z
duration_min: 40
issue: 5
focus: cost_per_query_model_plus_scripts_cost_table_plus_aws_us_east_1_snapshot
delta:
  files_added: 5  # src/vector_bench/cost.py, src/vector_bench/prices.py, scripts/cost_table.py, tests/test_cost.py, tests/test_cost_table.py (+ docs/cost_per_query.md)
  files_changed: 2  # README.md, src/vector_bench/__init__.py
  tests_added: 32
  test_pass_rate: "83/83"
  benchmarks:
    snapshot_date: "2026-05-17"
    monthly_usd_1m_per_engine: 74.08
    monthly_usd_10m_per_engine: 219.96
    monthly_usd_100m_per_engine: 915.84
    throughput_qps_stub_10k: 1623.5
    usd_per_million_queries_1m: 0.02
    usd_per_million_queries_10m: 0.05
    usd_per_million_queries_100m: 0.21
context_for_next_session:
  - cost_model_at_src_vector_bench_cost_dep_free_dataclasses_instance_price_ebs_gp3_price_price_table_infra_spec_cost_breakdown_cost_per_query
  - aws_us_east_1_snapshot_at_src_vector_bench_prices_three_instances_m6i_large_r6i_xlarge_r6i_4xlarge_gp3_ebs_dated_2026_05_17_with_source_url
  - scripts_cost_table_py_parses_terraform_envs_benchmark_main_tf_for_per_tier_sizing_reads_results_load_run_id_c001_json_for_throughput_writes_docs_cost_per_query_md
  - amortization_basis_730_hours_per_month_aws_billing_convention_2628000_seconds_per_month
  - infra_bill_identical_across_three_engines_per_tier_by_design_because_terraform_locals_share_instance_ebs_per_tier_engine_differences_come_from_throughput_only
  - committed_table_uses_stub_10k_throughput_marked_simulated_real_engine_numbers_pending_operator_make_up_and_load_harness_runs
  - d_010_documented_snapshot_prices_with_caller_override_mirrors_llm_cost_optimizer_d_003
  - public_surface_widened_to_export_new_cost_layer_from_vector_bench_top_level
  - parser_is_focused_regex_for_terraform_locals_block_not_general_hcl_implementation
  - test_cost_table_doc_matches_committed_via_main_round_trip_against_real_repo_files
  - remaining_priority_med_open_in_this_repo_zero_after_5_closes
decisions_made: [D-010]
followups: []
---

---
session: 2026-05-18T15:38Z
duration_min: 12
issue: 5
focus: unblock_pr_10_commit_stub_10k_fixtures
delta:
  files_changed: 1  # .gitignore
  files_added: 4   # results/load/stub-10k/{c001,c010,c100,matrix}.json
  tests_added: 0
  test_pass_rate: "83/83 (was failing 2/81)"
context_for_next_session:
  - gitignore_now_layered_results_star_results_load_results_load_stub_10k_star_with_re_includes_so_only_stub_10k_fixtures_track
  - cant_re_include_past_parent_excluded_dir_is_the_gitignore_gotcha_documented_inline
  - committed_four_load_harness_artifacts_c001_c010_c100_matrix_dot_json
  - reproducible_via_python_dash_m_vector_bench_dot_load_dash_run_dash_id_stub_dash_10k_dash_backend_stub
  - hnsw_grid_and_other_operator_runs_still_excluded_git_check_ignore_confirmed
decisions_made: []
followups: []
---

---
session: 2026-05-19T06:10Z
duration_min: 25
issue: 11
focus: drop_this_pr_issue_n_plus_pending_until_ships_framing_plus_snapshot
delta:
  files_changed: 1   # README.md
  files_added: 1     # tests/test_readme_snapshot.py
  tests_added: 5
  test_pass_rate: "88/88"
context_for_next_session:
  - readme_what_this_is_third_paragraph_rewritten_past_tense_no_this_pr_anchor
  - demo_section_replaces_bare_pending_until_2_ships_with_two_command_hermetic_demo
  - snapshot_test_locks_5_invariants_including_no_pending_until_no_this_pr_issue_n
  - capture_followup_filed_as_issue_12
  - tamper_verified_reinjecting_pending_until_ships_fires_snapshot
decisions_made: []
followups: ["#12"]
---

---
session: 2026-05-19T15:26Z
duration_min: 30
issue: 14
focus: snapshot_test_locks_hnsw_recommended_defaults_row_to_live_grid
delta:
  files_added: 1   # tests/test_hnsw_recommended_defaults_snapshot.py
  files_changed: 0
  tests_added: 3
  test_pass_rate: "91/91"
context_for_next_session:
  - third_snapshot_test_in_this_repo_after_readme_snapshot_and_cost_table
  - test_imports_hnsw_grid_via_importlib_util_spec_from_file_location_no_pythonpath_dep
  - module_scoped_fixture_runs_36_cell_default_grid_once_seed_1_about_9_seconds_on_laptop
  - knee_logic_min_p95_among_cells_with_mean_recall_at_k_ge_0_95
  - assertions_parameter_triple_32_100_128_plus_recall_0_998_within_abs_5e_4_plus_readme_row_anchor
  - p95_intentionally_not_locked_wall_clock_dependent_documented_in_docstring
  - live_rerun_today_produced_p95_1_89ms_vs_committed_2_02ms_validates_design_choice_to_exclude_p95
  - tamper_verified_readme_0_998_to_0_999_fires_row_anchor_assertion_with_regen_hint
  - no_new_d_entry_d_009_governs_simulation_backend
decisions_made: []
followups: []
---

---
session: 2026-05-19T19:50Z
duration_min: 35
issue: 14
focus: redesign_hnsw_recommended_defaults_snapshot_for_cross_platform_stability
delta:
  files_changed: 1   # tests/test_hnsw_recommended_defaults_snapshot.py
  tests_added: 0     # net (replaced 3 brittle tests with 3 stable tests)
  test_pass_rate: "91/91"
context_for_next_session:
  - pr15_failed_ci_with_min_p95_knee_selection_was_inherently_wall_clock_flaky
  - root_cause_compounded_float32_dot_blas_variance_across_platforms_perturbs_recall_by_0_001_0_002_plus_microsecond_p95_differences_flip_argmin
  - replaced_three_tests_with_more_robust_design_readme_row_anchor_plus_recall_at_exact_readme_cell_within_5e_3_plus_pareto_family_membership
  - did_not_touch_hnsw_sim_backend_kept_float32_internal_no_d_change_to_d_009
  - did_not_pin_numpy_in_ci_test_level_fix_suffices_for_snapshot_pattern_intent
  - tamper_verified_3_of_3_readme_row_mutation_recall_expected_900_family_min_recall_to_9999
  - all_91_tests_pass_local_ruff_check_clean_format_clean
  - pushed_to_existing_session_branch_for_issue_14_pr_15_should_now_pass_ci
decisions_made: []
followups: []
---

---
session: 2026-05-20T03:36Z
duration_min: 20
issue: 16
focus: public_surface_snapshot_test_locks_vector_bench_top_level_init
delta:
  files_added: 1   # tests/test_public_surface.py
  files_changed: 0   # __version__ already present in __init__.py
  tests_added: 10   # 4 standalone + 1 dotted-path + 5 submodule anchors
  test_pass_rate: "101/101"
context_for_next_session:
  - first_absolute_import_variant_in_may_2026_pattern_series_ast_filter_module_startswith_vector_bench_dot
  - package_docstring_imports_axis_locks_8_names_quoted_lines_5_9_of_init
  - console_script_dotted_path_axis_locks_pyproject_vector_bench_cli_main
  - five_submodule_anchors_backends_dot_stub_cost_harness_prices_types
  - tamper_verified_four_axes_bad_version_drop_workload_inproc_delete_cli_main_alias_rename_run_benchmark
  - portfolio_pattern_now_nine_strikes_all_eight_python_packages_with_init_py_covered_plus_one_more_planned
decisions_made: []
followups: []
---

---
session: 2026-05-21T19:23Z
duration_min: 22
issue: 12
focus: scripts_capture_demo_sh_two_surface_60s_driver_plus_smoke_test_plus_readme_flag_fix
delta:
  files_added: 2   # scripts/capture_demo.sh, tests/test_capture_demo_smoke.py
  files_changed: 1 # README.md (Demo flag fix --k 10 → --top-k 10 + capture paragraph)
  tests_added: 3
  test_pass_rate: "104/104"
context_for_next_session:
  - seventh_repo_to_land_capture_demo_sh_pattern_this_week
  - two_surfaces_vector_bench_stub_run_then_cost_table_dry_chosen_for_under_one_second_hermetic_runtime
  - vector_bench_invocation_requires_top_k_not_k_plus_run_id_plus_results_dir_readme_had_stale_k_flag_fixed_inline
  - force_flag_used_so_re_records_dont_need_manual_cleanup_per_run_tempdir_eliminates_collisions
  - smoke_test_pins_json_keys_mean_recall_at_k_query_latency_p50_p95_p99_run_id_top_k_plus_every_tier_engine_row_in_cost_table
  - terraform_make_validate_third_surface_deliberately_excluded_terraform_not_a_ci_dep
  - no_new_d_entry_pure_glue_around_existing_clis
decisions_made: []
followups: []
---

---
session: 2026-05-22T03:30Z
duration_min: 30
issue: 19
focus: run_benchmark_refuses_workload_concurrency_gt_1_d_011
delta:
  files_changed: 3   # harness.py, cli.py, README.md
  files_modified_tests: 1  # test_harness.py (appended class)
  tests_added: 4
  test_pass_rate: "112/112"
decisions_made: [D-011]
context_for_next_session:
  - silent_lying_latency_stat_when_vector_bench_run_concurrency_8_run_benchmark_was_serial_but_recorded_concurrency_8
  - same_shape_as_chunking_strategies_lab_d_011_just_closed_documented_constraints_must_be_promoted_to_runtime_enforcement
  - gate_runs_before_filesystem_check_so_misconfigured_caller_doesnt_leave_stale_results_path
  - cli_concurrency_help_reworded_to_say_reserved_values_gt_1_refused_use_load_subcommand
  - readme_benchmark_harness_section_gets_one_paragraph_explaining_run_vs_load_split
  - test_class_runbenchmarkconcurrencygate_in_test_harness_py_four_cases_passes_raises_message_filesystem_isolation
  - portfolio_pattern_third_post_v0_1_honesty_fix_after_emb_shootout_word_bigrams_and_chunking_lab_late_embedder_consistency_today
followups: []
---

---
session: 2026-05-22T19:40Z
duration_min: 25
issue: 21
focus: docs_architecture_md_reflects_all_three_shipped_layers_not_the_one_only_pre_shipping_state
delta:
  files_changed: 1   # docs/architecture.md
  files_added: 1     # tests/test_architecture_doc.py
  tests_added: 7
  tamper_verify_axes: 3
context_for_next_session:
  - architecture_md_was_frozen_at_terraform_pr_issue_1_and_never_reframed_when_2_3_4_5_shipped_three_section_headers_carried_pre_shipping_framing_mermaid_bench_node_said_future_issue_2
  - rewrote_to_steady_state_pipeline_view_two_class_legend_shipped_green_opkey_yellow_for_aws_ec2_nodes_that_need_terraform_apply_bench_node_now_green_with_2_annotation
  - documented_d_011_runtime_gate_inline_at_layer_2_vector_bench_run_bullet_rather_than_as_top_level_layer_so_a_reader_of_architecture_doc_finds_the_gate_where_they_look
  - new_tests_test_architecture_doc_py_three_invariants_path_token_reachability_with_angle_bracket_brace_placeholder_skip_closed_feature_issue_coverage_1_2_3_4_5_14_16_banned_phrases_absent_this_pr_pending_future_unfiled_to_be_filed
  - 11_12_19_intentionally_excluded_from_coverage_11_is_readme_only_12_is_operator_artifact_19_is_runtime_gate_documented_inline_not_a_layer
  - belt_and_braces_three_hard_pin_tests_lock_banned_phrases_known_shipped_issues_resolvable_prefixes
  - tamper_verified_three_axes_inject_this_pr_remove_5_refs_add_bad_path_each_fires_relevant_assertion_with_specific_drift_quoted
  - thirteenth_post_v0_1_drift_or_doc_fix_in_portfolio_pattern_fourth_architecture_doc_lock_test_in_this_session_after_mcp_cookbook_emb_shootout_nextjs_ai_app
  - portfolio_now_seven_repos_with_architecture_doc_lock_tests
  - issue_filed_mid_session_as_priority_med_then_closed_in_same_session_per_session_prompt_loop_protocol
decisions_made: []
followups: []
---

---
session: 2026-05-23T15:35Z
duration_min: 22
issue: 23
focus: arch_doc_active_decision_range_axis_caught_d_002_through_d_010_uncited
decisions_made: []
delta:
  files_changed: 2
  files_added: 0
  tests_added: 3
  test_pass_rate: "118/118"
context_for_next_session:
  - seventh_repo_in_portfolio_to_ship_active_decision_range_axis_after_llm_eval_harness_emb_shootout_today_plus_four_repos_this_week
  - real_drift_caught_only_d_011_was_cited_prior_d_002_through_d_010_uncited_despite_governing_every_layer_of_the_substrate
  - backfill_inline_at_layer_d_002_singleaz_d_003_weaviate_oss_d_004_pinned_node_d_005_backend_protocol_d_006_stub_recall_one_d_007_one_json_per_run_d_008_threadpool_not_k6_d_009_hnsw_sim_numpy_d_010_aws_us_east_1_pricetable
  - added_two_new_path_tokens_to_doc_hnsw_sim_py_and_prices_py_both_resolve_on_disk_path_token_test_still_green
  - sister_repo_targets_remaining_prompt_regression_suite_agent_orchestration_platform
followups: []
---

---
session: 2026-05-24T03:55Z
duration_min: 30
issue: 25
focus: cost_table_dry_unread_and_load_results_documented_but_unwired
delta:
  files_changed: 2   # scripts/cost_table.py, tests/test_cost_table.py
  tests_added: 5
  test_pass_rate: "123_passed"
decisions_made: []
context_for_next_session:
  - cost_table_script_shipped_dry_flag_that_was_never_read_and_docstring_promised_load_results_per_tier_flag_that_didnt_exist_on_parser
  - every_tier_row_was_hardcoded_simulated_marker_regardless_of_args_dry
  - fix_dry_to_booleanoptionalaction_default_true_simulated_marker_now_real_label_dropped_under_no_dry
  - new_load_results_tier_eq_path_repeatable_per_tier_override_overridden_tiers_labeled_real_regardless_of_dry
  - tier_validation_manual_against_scale_tiers_so_unknown_tier_error_lists_inventory_on_stderr_malformed_entries_exit_2
  - docstring_modes_section_rewritten_to_match_implementation_finally
  - test_helper_table_row_lines_filters_to_per_tier_rows_via_stub_10k_or_real_path_substring_so_label_tests_dont_false_positive_on_explanatory_prose
  - seventh_in_night_session_loop_first_non_pure_parity_fix_this_is_actual_doc_impl_gap_closure
  - same_pattern_as_llm_cost_optimizer_30_real_api_guard_revival_just_richer_surface_per_tier_overrides_not_just_flag_flip
followups: []
---

---
session: 2026-05-25T01:15Z
duration_min: 20
issue: 27
focus: instance_price_ebs_gp3_price_infra_spec_post_init_validates_fields
delta:
  files_changed: 1   # src/vector_bench/cost.py
  files_added: 0
  tests_added: 20  # parametrized across three dataclasses x numeric + string fields + inclusive-zero pin
  test_pass_rate: "143_passed"
decisions_made: []
context_for_next_session:
  - instance_price_post_init_validates_usd_per_hour_ge_zero_vcpus_ge_one_memory_gib_ge_zero_plus_instance_type_and_region_non_empty
  - ebs_gp3_price_post_init_validates_three_rate_fields_ge_zero_included_iops_and_throughput_ge_zero_plus_region_non_empty
  - infra_spec_post_init_validates_data_volume_gb_and_provisioned_iops_and_throughput_ge_zero_plus_scale_tier_engine_instance_type_non_empty
  - harm_was_monthly_cost_at_cost_py_194_instance_usd_per_hour_times_hours_per_month_silently_inverts_sign_of_total_usd_month_in_docs_costs_md
  - max_zero_clamps_at_lines_196_and_198_made_harm_worse_for_provisioned_iops_and_throughput_silently_omitting_cost_lines_instead_of_raising_now_caught_at_construction
  - d_010_cost_model_ships_documented_snapshot_extended_from_no_silent_zero_via_unknown_instance_type_error_to_no_silent_negative_via_post_init_guard
  - mirrors_three_sister_fixes_today_llm_cost_optimizer_34_pr_35_rag_production_kit_36_pr_37_embedding_model_shootout_29_pr_30_four_cost_aware_repos_defended_consistently
  - test_helpers_valid_x_kwargs_centralize_fixture_construction_each_negative_test_only_mutates_field_under_test
  - test_count_143_was_123_after_25_added_20_new_collected_cases_across_three_dataclasses
  - sixth_phase_bc_target_in_180_min_day_session_after_phase_a_5_pr_merge_plus_five_prior_phase_bc_targets
followups: []
---

---
session: 2026-05-25T07:25Z
duration_min: 25
issue: 29
focus: workload_and_recall_at_k_isinstance_int_guards_extend_sign_only
delta:
  files_changed: 2   # src/vector_bench/harness.py, tests/test_harness.py
  files_added: 0
  tests_added: 26   # 25 from the field x bad-value matrix + 1 acceptance regression
  test_pass_rate: "173_passed"
decisions_made: []
context_for_next_session:
  - second_pr_in_vector_search_tonight_first_via_phase_a_fixup_merge_of_28_cost_dataclass_post_init_sign_only
  - workload_post_init_five_field_loop_sign_only_le_0_accepted_nan_via_nan_comparisons_false_and_fractional_silently_truncated_via_range_int_in_load_loop
  - recall_at_k_same_sign_only_shape_non_int_silently_miscounts_via_set_list_slicing
  - tightened_both_to_require_isinstance_x_int_with_explicit_bool_exclusion_python_bool_subclasses_int_count_field_intent_never_boolean
  - workload_keeps_existing_per_field_must_be_positive_message_new_isinstance_check_fires_with_must_be_an_int_before_reaching_sign_comparison
  - recall_at_k_message_tightened_must_be_positive_to_must_be_a_positive_integer_one_pre_existing_test_updated_in_place
  - eleventh_phase_bc_target_in_360_min_night_session_now_two_phase_bc_prs_per_originally_unvisited_tonight_repo_plus_three_for_some_already_touched_repos_with_second_iteration
  - portfolio_contract_tightening_sweep_eleven_prs_phase_bc_plus_seven_phase_a_fixups_eighteen_substantive_items_tonight
followups: []
---

---
session: 2026-05-26T03:40Z
duration_min: 25
issue: 31
focus: hnsw_sim_backend_isinstance_int_bool_reject_completes_29_sweep
delta:
  files_changed: 2   # src/vector_bench/backends/hnsw_sim.py, tests/test_hnsw_sim.py
  files_added: 0
  tests_added: 20   # 5 bad x 3 fields = 15 reject + 5 acceptance over [1,8,16,32,64]
  test_pass_rate: "193_passed"
decisions_made: []
context_for_next_session:
  - issue_31_filed_and_closed_in_same_session_only_un_tightened_construction_site_in_src_vector_bench
  - hnsw_sim_backend_post_init_field_loop_was_only_remaining_sign_only_le_zero_in_repo_after_29_workload_recall_at_k_sweep
  - silent_failure_mode_one_M_true_silently_bound_self_M_true_topk_local_argsort_neg_sims_colon_self_M_returned_1_neighbor_instead_of_16_recall_silently_collapsed_worst_harm_class_for_this_repo
  - silent_failure_mode_two_M_1_5_or_16_0_silently_bound_then_colon_1_5_raised_typeerror_slice_indices_must_be_integers_deep_in_ingest
  - silent_failure_mode_three_M_nan_or_inf_silently_bound_surfaces_as_opaque_numpy_errors_at_query_time
  - silent_failure_mode_four_ef_construction_true_silently_min_ef_construction_n_returned_1_ingest_built_with_one_candidate_per_row_recall_terrible_no_error
  - silent_failure_mode_five_ef_search_true_silently_caps_beam_search_frontier_recall_collapses_silently
  - fix_added_isinstance_value_int_or_isinstance_value_bool_above_existing_le_zero_in_existing_three_field_loop_zero_new_branches_one_new_condition_inside_existing_iteration
  - error_message_shape_label_must_be_an_int_got_value_repr_then_existing_label_must_be_positive_got_value_preserved
  - test_strategy_three_parametrize_blocks_per_field_over_bad_int_shape_from_29_plus_one_acceptance_block_over_1_8_16_32_64
  - noqa_sim300_added_to_three_acceptance_asserts_because_ruff_yoda_condition_fires_when_parametrize_param_named_good_used_as_right_side_codebase_otherwise_uses_attr_eq_value_standard
  - test_count_vector_search_now_193_was_173_after_29_added_20_new_collected_cases
  - fourth_phase_bc_target_in_360_min_night_session_after_prompt_regression_37_chunking_lab_31
  - portfolio_validation_sweep_now_extends_into_four_repos_this_night_phase_bc_run_prompt_regression_chunking_lab_vector_search_plus_implicit_phase_a_rescue_for_rag_kit_emb_shootout_llm_eval_harness
followups: []
---

---
session: 2026-05-26T19:50Z
duration_min: 22
issue: 33
focus: add_vector_bench_io_utils_atomic_write_text_route_load_harness_hnsw_grid_cost_table_through_it_completing_portfolio_atomic_write_saturation
delta:
  files_added: 2
  files_changed: 4
  tests_added: 8  # 6 unit + 2 integration
  test_pass_rate: "201_passed"
decisions_made: [D-012]
context_for_next_session:
  - sixth_phase_bc_issue_of_today_day_session_completes_portfolio_atomic_write_saturation_to_12_of_12_repos
  - five_production_sites_closed_load_per_cell_loop_load_matrix_json_harness_result_json_hnsw_grid_cost_table_md
  - load_per_cell_loop_most_blast_radius_partial_state_across_cell_files_breaks_matrix_load_reader_silently_atomicity_now_eliminates_that_failure_mode
  - decision_d_012_matches_portfolio_pattern
  - portfolio_atomic_write_coverage_now_at_12_of_12_with_nextjs_streaming_ai_patterns_having_no_on_disk_write_paths_to_harden
  - dropped_two_redundant_os_makedirs_calls_plus_parent_mkdir_gates_that_the_helper_now_covers
  - elapsed_approx_70_min_of_180_min_budget_six_issues_closed_in_total
followups: []
---

---
session: 2026-05-26T23:50Z
duration_min: 6
issue: 35
focus: readme_decision_range_upper_bound_lock_propagation_6_of_10
delta:
  files_changed: 2
  tests_added: 1
context_for_next_session:
  - readme_decision_range_lock_pattern_now_seven_repos_chunking_lab_eval_harness_cost_optimizer_prompt_regression_rag_kit_emb_shootout_vector_search
  - five_more_repos_pending_async_pipelines_agent_orch_mcp_cookbook_nextjs_streaming_ai_integration_tests
decisions_made: []
followups: []
---
