# Repository Intelligence CLI Tool (v3.0.0)

A high-performance, multi-stage static analysis pipeline designed to transform raw codebases into actionable intelligence. This tool performs deep analysis on Git metadata, code metrics, architectural patterns, and security risks.

## 🛠️ Setup & Requirement

### 1. Python Environment

Ensure you have Python 3.8+ installed. Install the required libraries:

### 2. Create Virtual Environment

```powershell
python -m venv venv
```

### 3. Activate Virtual Environment

```
.\venv\Scripts\Activate.ps1
```

### 4. Variable Naming Consistency

```powershell
pip install -r requirements.txt
```

### 5. Install `cloc` (Critical for accuracy)

The tool uses `cloc` to calculate precise "Ground Truth" line counts and language breakdowns.

#### Windows Setup

* **Recommendation**: Install via Chocolatey:
  ```powershell
  choco install cloc
  ```

  Or place `cloc.exe` in the tool directory or your system's PATH.

#### macOS Setup

If you are using macOS, you can install `cloc` using Homebrew:

* **Install Homebrew (if not installed)**:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
* **Install cloc**:
  ```bash
  brew install cloc
  ```

#### Linux Setup

For Linux distributions, install `cloc` via the package manager:

* **Debian/Ubuntu**:
  ```bash
  sudo apt update && sudo apt install -y cloc
  ```
* **RedHat/Fedora**:
  ```bash
  sudo dnf install -y cloc
  ```
* **Arch Linux**:
  ```bash
  sudo pacman -S cloc
  ```

#### Verify Installation

```bash
cloc --version
```

If the version number is displayed successfully, `cloc` is installed correctly.

### 6 Git Installation

Ensure `git` is installed and available in your terminal so the tool can clone remote repositories and analyze commit history.

## 📖 Usage

### Interactive Menu (Recommended)

Simply run the tool without arguments for an easy-to-use menu:

```powershell
python Repo_analysis_tool.py
```

### Batch Mode

To analyze multiple repositories at once, create a `repos.txt` file (one URL or path per line):

```powershell
python Repo_analysis_tool.py --batch repos.txt -o ./outputs
```

### CLI Commands

* **Analyze Local Directory**:
  ```bash
  python Repo_analysis_tool.py -i "C:\path\to\project" -o .\outputs --mode full
  ```
* **Analyze Remote Repository**:
  ```bash
  python Repo_analysis_tool.py -i https://github.com/user/repo.git --mode full
  ```
* **Analyze Remote Repository with GitHub PR Analytics**:
  Set the environment variable (or pass `--github-token`) to run the PR analytics Stage 0.5:
  ```powershell
  $env:GITHUB_TOKEN="your_pat_token"
  python Repo_analysis_tool.py -i https://github.com/user/repo.git
  ```

## 🚀 Key Features

### 1. Multi-Stage Analysis Pipeline

The tool executes analysis in organized layers to ensure a separation between ground truth (verified tools) and heuristic estimates:

- **Stage 0 (Git Meta)**: Extracts commit counts, unique & total contributor diversity, active span, and history integrity.
- **Stage 0.5 (GitHub PR Analytics)**: Optionally extracts Pull Request metrics (total, open, closed, merged counts) and dumps full PR metadata to OS-safe JSON files if GITHUB_TOKEN or GH_TOKEN env is available.
- **Stage 1 (Structure)**: Scans directory hierarchy for architectural signals and framework manifests.
- **Stage 2 (Deep Metrics)**: Calculates verified LOC (via `cloc`), LLM token density (via `tiktoken`), and cross-file duplication.
- **Stage 3 (AI Detection)**: Uses entropy and token distribution heuristics to identify AI-generated code.
- **Stage 4 (Intelligence)**: Categorizes Frontend vs. Backend logic, detects infrastructure (Databases, Cloud, APIs) at all depths, and evaluates documentation quality.
- **Stage 5 (Security)**: Scans for exposed credentials, AWS keys, and database connection strings.

### 2. Advanced Infrastructure Detection (v3.0.0)

- **Canonical Reporting**: Automatically groups database aliases (e.g., `postgres` and `postgresql` → `PostgreSQL`).
- **Greedy Scanning**: Peeks inside source code files (`.py`, `.js`, `.go`, etc.) to identify library imports and connection strings.
- **Full-Depth Scanning**: Recursively analyzes the entire repository without depth limits.

### 3. Professional CSV Reporting

Generates standardized CSV outputs for at-scale repository auditing:

- **`summary_all.csv`**: A comprehensive dataset featuring 40 parameters including contributor mapping, complexity, architectural splits, and security findings.
- **`summary_metadata.csv`**: A curated executive summary focused on commercial usage, security status, and core architectural labels.

*For a detailed breakdown of what each CSV column means, please refer to the [`csv_schema.md`](./csv_schema.md) document.*
*For more technical insights on how the tool processes data, read the [`architecture.md`](./architecture.md).*

### 4. Robust Input Handling

- **Quote-Resistant Paths**: Automatically strips double-quotes from paths pasted from Windows File Explorer ("Copy as path").
- **Batch Processing**: Supports a single `.txt` file containing a mix of local directory paths and remote Git URLs.

## 📊 Outputs

The tool generates professional-grade reports in the `./outputs` folder:

1. **`summary_all.csv`**: Master dataset for data processing and audit reporting.
2. **`summary_metadata.csv`**: Curated metadata report for executive review.
3. **`{repo}_report.json`**: Deep-dive technical breakdown for each analyzed repository.

---

*Developed for Advanced Repository Intelligence & Technical Auditing.*
