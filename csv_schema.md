# CSV Column Schema & Abbreviations

This document explains the meaning behind the columns generated in the `summary_all.csv` and `summary_metadata.csv` outputs by the Repository Analysis Tool.

## General Information
*   **repo_name**: The name of the analyzed repository or folder.
*   **is_git**: Boolean (`True`/`False`) indicating whether a `.git` folder and valid git history were found.
*   **license_type**: Detected open-source or commercial license (e.g., MIT, Apache License, Unknown).

## Git Metadata & Timeline
*   **first_commit_date**: Timestamp of the very first commit in the repository.
*   **last_commit_date**: Timestamp of the most recent commit.
*   **development_span_months**: The active lifespan of the repository in months (from first commit to last).
*   **commits**: Total number of commits across the default branch.
*   **meaningful_commit_count**: Commits excluding generic/boilerplate messages like "Update README" or "Merge branch".
*   **commits_per_month**: Average commit velocity per active month.
*   **branch_count**: Total number of git branches discovered in the local repository.
*   **git_history_intact**: Boolean indicating if the git history appears continuous without suspicious single-bulk commits or extreme force-pushes.

## Contributors
*   **contributors**: Count of unique contributor email addresses extracted from Git history. Note that developers using multiple emails might be counted multiple times.
*   **all_contributors_count**: Total number of contributor identities (combining names and emails) found.
*   **all_contributors**: A comma-separated list of contributor names (truncated in the terminal, full list in CSV).

## Code Size & Tokens
*   **loc_code**: Lines of executable code (verified via CLOC).
*   **loc_comment**: Lines of comments and docstrings.
*   **loc_blank**: Blank lines in the codebase.
*   **loc_files**: Total number of source files analyzed.
*   **tokens_llm**: Count of tokens calculated using the `cl100k_base` (GPT-4) tokenizer. Useful for LLM context window sizing.
*   **lexical_token**: Raw count of code symbols, operators, and identifiers.

## Languages & Frameworks
*   **lang_count**: Total distinct programming languages detected.
*   **languages**: Pipe-separated list of all core programming languages used.
*   **languages_frontend**: Languages specifically categorized for User Interface / Frontend (e.g., HTML, CSS, JavaScript).
*   **languages_backend**: Languages categorized for Server / Backend logic (e.g., Python, Go, Java).
*   **frameworks**: Detailed map of the frameworks found, categorized by where they were detected (e.g., `package.json`, `requirements.txt`, or direct `code_imports`).
*   **framework_frontend**: High-level frontend frameworks detected (e.g., React, Vue, Next.js).
*   **framework_backend**: High-level backend frameworks detected (e.g., Django, Express, FastAPI).
*   **languages_percentage_bytes(>5%)**: The proportion of repository size occupied by each major language (ignoring trace languages < 5%).
*   **frameworks_percentage_bytes(>5%)**: The proportion of repository size occupied by code associated with a specific framework.

## Infrastructure & Integrations
*   **databases_used**: Detected database technologies (e.g., PostgreSQL, Redis, MongoDB).
*   **third_party_apis**: Integrations with external SaaS providers (e.g., Firebase, Stripe, Twilio).
*   **setup_guidelines**: Indicates if instructions for environment setup or deployment exist in the documentation (`Present` or `Not found`).

## Security, Quality, & Heuristics
*   **security_findings**: Summarizes secret exposures. `Clean` if no secrets found. `Review Required (N)` if N potential secrets (e.g., AWS keys, database URIs) were detected.
*   **documentation_quality**: Estimated quality rating (`High`, `Medium`, `Low`) based on README length, structure, and presence of setup guides.
*   **duplication_weighted_percent**: Percentage of the codebase consisting of duplicated blocks/files, weighted by token size.
*   **code_complexity**: A heuristic rating (`Low`, `Moderate`, `High`) based on file lengths, line variance, and nested logic depth.
*   **ai_detection_percent**: An experimental metric predicting the probability that the codebase contains AI-generated segments based on entropy and uniformity.
*   **repo_rating_score**: A composite score (0-10) factoring in Git health, LOC size, testing, and framework presence.
*   **repo_rating_label**: Categorical mapping of the rating score (e.g., `Poor`, `Fair`, `Good`, `Excellent`).

## Process Metrics
*   **total_time_seconds**: Execution time taken by the pipeline to analyze this repository.

## GitHub PR Analytics
*   **total_pr_count**: Total number of Pull Requests found on GitHub for this repository.
*   **open_pr_count**: Number of currently open Pull Requests.
*   **closed_pr_count**: Number of closed Pull Requests.
*   **merged_pr_count**: Number of merged Pull Requests (a subset of closed Pull Requests).
*   **github_pr_analysis_available**: Boolean indicating if GitHub Pull Request analytics were successfully run for the repository.

## Extra Metadata Columns (`summary_metadata.csv` specific)
*   **commercial_usage_summary**: Simplifies license detection into `Permissive` (MIT, Apache, etc.) or `Restrictive`.
*   **domain_industry**: (Placeholder) Future-ready field for classifying the industry context of the application.
*   **language_framework_details**: Detailed pipe-separated raw output of how languages and frameworks were identified.
