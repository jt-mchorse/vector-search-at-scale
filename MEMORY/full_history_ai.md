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
