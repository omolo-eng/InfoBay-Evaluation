import os
import sys
import json
import csv
import argparse
import hashlib
import re
import subprocess
import time
import logging
import multiprocessing
import threading
import datetime
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional, Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from github import Github, RateLimitExceededException, GithubException
    HAS_PYGITHUB = True
except ImportError:
    HAS_PYGITHUB = False

    class GithubException(Exception):
        pass

    class RateLimitExceededException(GithubException):
        pass

    class Github:
        pass

CSV_LOCK = threading.Lock()

try:
    import tiktoken
    HAS_TIKTOKEN = True
    ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    HAS_TIKTOKEN = False
    ENCODING = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 5 * 1024 * 1024
SIMILARITY_THRESHOLD = 0.85
MIN_LOC = 3
MIN_TOKENS = 20
MAX_SIMILARITY_PAIRS = 50_000
MAX_BUCKET_COMPARE = 300

GIT_LOG_TIMEOUT = 120
CODE_AGE_THRESHOLDS_YEARS = [1, 2]

# Stage 4: Composite rating criteria
RATING_CRITERIA = {
    "commits":           {"min": 75,   "preferred": 200,    "weight": 0.20},
    "dev_span_months":   {"min": 6,    "preferred": 24,     "weight": 0.15},
    "contributors":      {"min": 2,    "preferred": 5,      "weight": 0.15},
    "loc":               {"min": 5_000, "preferred": 50_000, "weight": 0.20},
    "has_tests":         {"weight": 0.10},
    "has_ci":            {"weight": 0.10},
    "test_file_count":   {"min": 20,   "preferred": 200,    "weight": 0.05},
    "source_file_ratio": {"min": 0.40, "preferred": 0.60,   "weight": 0.05},
}

# Stage 4: Framework detection — manifest → framework list
FRAMEWORK_MANIFEST_PARSERS = {
    "package.json": [
        "react", "vue", "angular", "svelte",
        "next", "nuxt", "gatsby", "remix", "astro",
        "express", "fastify", "nestjs", "koa", "hapi",
    ],
    "requirements.txt": [
        "django", "flask", "fastapi", "tornado",
        "starlette", "aiohttp", "pyramid", "falcon", "bottle"
    ],
    "Pipfile": [
        "django", "flask", "fastapi"
    ],
    "pom.xml": [
        "spring", "springboot", "quarkus", "micronaut"
    ],
    "build.gradle": [
        "spring", "springboot", "quarkus", "micronaut"
    ],
    "Cargo.toml": [
        "actix", "axum", "warp", "rocket"
    ],
    "go.mod": [
        "gin", "echo", "fiber", "chi", "beego"
    ],
    "Gemfile": [
        "rails", "sinatra", "hanami"
    ],
    "composer.json": [
        "laravel", "symfony", "codeigniter", "yii"
    ],
    "mix.exs": [
        "phoenix"
    ],
    "stack.yaml": [
        "yesod", "scotty"
    ],
}

IMPORT_FRAMEWORK_MAP = {
    "py": {
        "django": "Django",
        "flask": "Flask",
        "fastapi": "FastAPI",
        "tornado": "Tornado",
        "starlette": "Starlette",
        "aiohttp": "aiohttp",
        "pyramid": "Pyramid",
        "falcon": "Falcon",
        "bottle": "Bottle"
    },
    "js": {
        "express": "Express",
        "next": "Next.js",
        "react": "React",
        "vue": "Vue",
        "angular": "Angular",
        "fastify": "Fastify",
        "nestjs": "NestJS",
        "koa": "Koa",
        "hapi": "Hapi"
    },
    "jsx": {"react": "React"},
    "tsx": {
        "react": "React",
        "next": "Next.js"
    }
}

FRONTEND_FRAMEWORKS = {
    "react", "vue", "angular", "svelte",
    "next", "nuxt", "gatsby", "remix", "astro"
}

BACKEND_FRAMEWORKS = {
    "express", "fastify", "nestjs", "koa", "hapi",
    "django", "flask", "fastapi", "tornado", "starlette", "aiohttp",
    "pyramid", "falcon", "bottle",
    "spring", "springboot", "quarkus", "micronaut",
    "actix", "axum", "warp", "rocket",
    "gin", "echo", "fiber", "chi", "beego",
    "rails", "sinatra", "hanami",
    "laravel", "symfony", "codeigniter", "yii",
    "phoenix", "yesod", "scotty"
}

EXT_TO_LANG_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.jsx': 'JSX',
    '.ts': 'TypeScript', '.tsx': 'TSX', '.go': 'Go',
    '.java': 'Java', '.c': 'C', '.cpp': 'C++', '.cc': 'C++',
    '.h': 'C/C++ Header', '.hpp': 'C/C++ Header',
    '.cs': 'C#', '.rb': 'Ruby', '.php': 'PHP', '.rs': 'Rust',
    '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.sass': 'Sass',
    '.less': 'LESS', '.sql': 'SQL', '.sh': 'Bourne Shell',
    '.ps1': 'PowerShell', '.bat': 'DOS Batch', '.json': 'JSON',
    '.xml': 'XML', '.md': 'Markdown', '.yaml': 'YAML', '.yml': 'YAML',
    '.swift': 'Swift', '.kt': 'Kotlin', '.scala': 'Scala', '.lua': 'Lua'
}

FORMAL_FRAMEWORK_NAMES = {
    "react": "React", "next": "Next.js", "vue": "Vue", "angular": "Angular",
    "svelte": "Svelte", "express": "Express", "fastapi": "FastAPI", "django": "Django",
    "flask": "Flask", "spring": "Spring Boot", "springboot": "Spring Boot",
    "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mongodb": "MongoDB",
    "redis": "Redis", "mysql": "MySQL", "sqlite": "SQLite", "firebase": "Firebase",
    "jest": "Jest", "vitest": "Vitest", "cypress": "Cypress", "playwright": "Playwright"
}

LANG_MERGE_MAP = {
    "C/C++ Header": "C++",
    "Jupyter Notebook": "Python",
    "JSX": "JavaScript",
    "TSX": "TypeScript"
}

FRONTEND_LANGUAGES = {
    "HTML", "CSS", "SCSS", "Sass", "Less",
    "JavaScript", "TypeScript",
    "JSX", "TSX",
    "Vue", "Svelte"
}

BACKEND_LANGUAGES = {
    "Python", "Java", "Go", "Rust", "Ruby", "PHP",
    "C++", "C", "C#", "Scala", "Kotlin",
    "Elixir", "Haskell", "Perl", "Clojure",
    "SQL", "Swift", "Dart"
}

NON_CORE_FORMATS = {
    "JSON", "XML", "YAML", "CSV", "TOML", "INI", "Protocol Buffers", "Graphviz (DOT)",
    "SVG", "SQL", "Properties", "Excel", "Parquet", "HCL", "Starlark",
    "Markdown", "Text", "reStructuredText", "AsciiDoc", "Doxygen", "Org", "TeX", "Org Mode",
    "Gradle", "ProGuard", "Windows Resource File", "Maven POM", "Protocol Buffers",
    "PowerShell", "Bourne Shell", "Bourne Again Shell", "Fish Shell", "DOS Batch", "make",
    "CMake", "Dockerfile", "Vagrantfile", "Procfile", "Rakefile", "Gemfile", "Pipfile",
    "BitBake", "Meson", "Kconfig", "QMake", "Bazel",
    "LESS", "SCSS", "Sass", "Stylus", "PostCSS"
}

BOILERPLATE_FILES = {
    "reportwebvitals.js", "setuptests.js", "app.test.js", "logo.svg",
    "favicon.ico", "service-worker.js", "manifest.json", "robots.txt",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "manage.py", "asgi.py", "wsgi.py", "__init__.py",
    "mvnw", "mvnw.cmd", "gradlew", "gradlew.bat",
    "settings.gradle", "gradle-wrapper.properties",
    "artisan", "server.php",
    "bundle", "rails", "rake", "setup"
}

VENDORED_NAMES = {
    "jquery", "bootstrap", "angular", "react.production", "react-dom.production",
    "vue.global", "vue.runtime", "lodash", "underscore", "backbone",
    "d3", "three", "moment", "chart", "highcharts", "leaflet",
    "popper", "tether", "select2", "datatables", "tinymce", "ckeditor",
    "ace-editor", "codemirror", "quill", "summernote", "sweetalert",
    "toastr", "animate", "font-awesome", "normalize", "reset",
    "polyfill", "modernizr", "respond", "html5shiv", "pace",
    "hammer", "howler", "socket.io", "fabric", "konva",
    "excalidraw", "wiris", "mathjax", "katex", "zoom-meeting",
    "dropbox-sdk", "aws-sdk", "firebase-app", "firebase-auth",
    "multislider",
}

VENDORED_DIRS = {
    "/vendor/", "/vendors/", "/third_party/", "/third-party/",
    "/bower_components/", "/jspm_packages/", "/web_modules/",
    "/custom_node_modules/", "/extern/", "/external/", "/lib/vendor/",
    "/.bundle/", "/cache/", "/deps/",
    "/site-packages/", "/dist-packages/", "/_vendor/",
}

BUNDLE_PATTERNS = {"bundle.js", "chunk.js", "vendor.js", "vendors.js", "runtime.js", "main.chunk.js", "vendors~main"}

# Stage 4: Infrastructure & Service Detection
INFRASTRUCTURE_INDICATORS = {
    "databases": [
        "postgres", "postgresql", "mysql", "mongodb", "redis", "sqlite", "elasticsearch",
        "dynamodb", "mariadb", "oracle", "mssql", "cassandra",
        "neo4j", "couchdb", "prisma", "sequelize", "mongoose", "psycopg2", "psycopg",
        "sqlalchemy", "typeorm", "knex", "tortoise-orm", "dj-database-url",
        "supabase", "pocketbase", "surrealdb", "cockroachdb", "fauna", "influxdb",
        "snowflake", "clickhouse", "databricks", "firebird", "trino", "presto"
    ],
    "deployment": [
        "docker", "kubernetes", "aws", "azure", "gcp", "heroku", "vercel",
        "netlify", "terraform", "ansible", "jenkins", "travis", "circleci",
        "github actions", "dockerfile", "docker-compose"
    ],
    "apis": [
        "stripe", "twilio", "sendgrid", "openai", "firebase", "auth0",
        "slack", "aws-sdk", "google-cloud", "mailgun", "algolia", "pusher"
    ]
}

INFRA_MANIFESTS_TO_CHECK = ["package.json", "requirements.txt", "Pipfile", "pom.xml","build.gradle", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "docker-compose.yml"
]
INFRA_CONFIG_FILES_TO_CHECK = [".env", "settings.py", "config.js","web.config", "appsettings.json", "config.php", "configuration.yaml"]

INFRA_DB_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".rdb"}

INFRA_CODE_EXTENSIONS = {".py", ".js", ".ts", ".php", ".go", ".java", ".rb", ".cs", ".sql"}

INFRA_CONN_MAP = {
    "postgres://": "postgres",
    "postgresql://": "postgres",
    "mysql://": "mysql",
    "mongodb://": "mongodb",
    "redis://": "redis"
}

INFRA_FILE_MAP = {
    "dockerfile": ("deployment", "docker"),
    "docker-compose": ("deployment", "docker-compose"),
    "jenkinsfile": ("deployment", "jenkins"),
    ".travis.yml": ("deployment", "travis"),
    "circle.yml": ("deployment", "circleci"),
    "terraform": ("deployment", "terraform"),
    ".github/workflows": ("deployment", "github actions")
}

INFRA_CANONICAL_MAP = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "psycopg2": "PostgreSQL", "psycopg": "PostgreSQL",
    "mongodb": "MongoDB", "mongoose": "MongoDB",
    "mysql": "MySQL", "mariadb": "MariaDB",
    "sqlite": "SQLite", "redis": "Redis", "elasticsearch": "Elasticsearch",
    "dynamodb": "DynamoDB", "cassandra": "Cassandra", "oracle": "Oracle",
    "mssql": "SQL Server", "neo4j": "Neo4j", "couchdb": "CouchDB",
    "firebase": "Firebase", "firestore": "Firebase",
    "prisma": "Prisma (ORM)", "sequelize": "Sequelize (ORM)", "sqlalchemy": "SQLAlchemy (ORM)",
    "typeorm": "TypeORM", "knex": "Knex.js", "docker": "Docker", "kubernetes": "Kubernetes",
    "github actions": "GitHub Actions", "docker-compose": "Docker Compose"
}

DOC_SETUP_KEYWORDS = ['setup', 'install', 'getting started', 'running', 'requirements']

COVERAGE_FILES = ['lcov.info', 'coverage.xml', 'cobertura.xml', '.coverage', 'coverage/index.html']

# Stage 4: Testing & Quality Patterns
TEST_CASE_PATTERNS = {
    "python": re.compile(r"def\s+test_"),
    "javascript": re.compile(r"(it|test)\s*\("),
    "typescript": re.compile(r"(it|test)\s*\("),
    "java": re.compile(r"@Test"),
    "go": re.compile(r"func\s+Test"),
    "php": re.compile(r"public\s+function\s+test"),
    "ruby": re.compile(r"test\s+['\"]"),
}

# Stage 5: Secret / credential scanning patterns
SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("AWS Access Key",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key",       re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]")),
    ("GitHub Token",         re.compile(r"ghp_[0-9a-zA-Z]{36}")),
    ("GitHub OAuth",         re.compile(r"gho_[0-9a-zA-Z]{36}")),
    ("GitHub App Token",     re.compile(r"ghu_[0-9a-zA-Z]{36}")),
    ("Slack Token",          re.compile(r"xox[baprs]-[0-9a-zA-Z\-]{10,48}")),
    ("Google API Key",       re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Stripe Secret Key",    re.compile(r"sk_live_[0-9a-zA-Z]{24,}")),
    ("Stripe Public Key",    re.compile(r"pk_live_[0-9a-zA-Z]{24,}")),
    ("Private Key Block",    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Generic Password",     re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}['\"]")),
    ("Generic API Key",      re.compile(r"(?i)(api_key|apikey|api-key)\s*[=:]\s*['\"][^'\"]{8,}['\"]")),
    ("Generic Secret",       re.compile(r"(?i)(secret|token)\s*[=:]\s*['\"][^'\"]{8,}['\"]")),
    ("DB Connection String", re.compile(r"(?i)(mongodb|postgres|mysql|redis)://[^\s'\"]{10,}")),
    ("Bearer Token",         re.compile(r"(?i)bearer\s+[0-9a-zA-Z\-._~+/]{20,}")),
]

SECRET_SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".php",
    ".java", ".kt", ".cs", ".env", ".sh", ".bash", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf", ".json",
}

SECRET_SCAN_SKIP = {".lock", ".sum", ".mod"}

# Language name mapping (extension → display name)
EXT_TO_LANGUAGE: Dict[str, str] = {
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".c++": "C++", ".hxx": "C++", ".h++": "C++", ".C": "C++", ".H": "C++",
    ".py": "Python", ".pyw": "Python", ".pyx": "Python",
    ".pxd": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy", ".gradle": "Groovy",
    ".clj": "Clojure", ".cljs": "Clojure", ".cljc": "Clojure", ".edn": "Clojure",
    ".cs": "C#",
    ".fs": "F#", ".fsx": "F#", ".fsi": "F#",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".zig": "Zig",
    ".nim": "Nim",
    ".v": "V",
    ".d": "D",
    ".ada": "Ada", ".adb": "Ada", ".ads": "Ada",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".fish": "Shell", ".ksh": "Shell", ".csh": "Shell", ".tcsh": "Shell",
    ".bat": "Batch", ".cmd": "Batch",
    ".ps1": "PowerShell",
    ".rb": "Ruby", ".erb": "Ruby", ".rake": "Ruby",
    ".php": "PHP", ".php3": "PHP", ".php4": "PHP", ".php5": "PHP", ".phtml": "PHP",
    ".pl": "Perl", ".pm": "Perl", ".t": "Perl", ".pod": "Perl",
    ".lua": "Lua",
    ".r": "R", ".R": "R",
    ".hs": "Haskell", ".lhs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".ex": "Elixir", ".exs": "Elixir",
    ".elm": "Elm",
    ".lisp": "Lisp", ".lsp": "Lisp", ".cl": "Lisp", ".el": "Lisp",
    ".scm": "Scheme", ".ss": "Scheme", ".rkt": "Scheme",
    ".re": "ReasonML", ".rei": "ReasonML",
    ".purs": "PureScript",
    ".dart": "Dart",
    ".jl": "Julia",
    ".f": "Fortran", ".for": "Fortran", ".f90": "Fortran",
    ".f95": "Fortran", ".f03": "Fortran", ".f08": "Fortran",
    ".sol": "Solidity",
    ".move": "Move",
    ".vh": "Verilog", ".sv": "Verilog",
    ".vhd": "VHDL", ".vhdl": "VHDL",
    ".asm": "Assembly", ".s": "Assembly", ".S": "Assembly",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".jsp": "JSP", ".asp": "ASP", ".aspx": "ASP.NET",
    ".sql": "SQL", ".psql": "SQL", ".mysql": "SQL", ".pgsql": "SQL",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".proto": "Protobuf",
    ".tf": "Terraform", ".tfvars": "Terraform", ".hcl": "HCL",
    ".sls": "SaltStack",
    ".ipynb": "Jupyter",
    ".pas": "Pascal", ".pp": "Pascal", ".inc": "Pascal",
    ".cob": "COBOL", ".cbl": "COBOL", ".cpy": "COBOL",
    ".cr": "Crystal",
    ".vim": "Vimscript",
    ".tcl": "Tcl",
    ".awk": "AWK",
    ".sed": "Sed",
    ".mk": "Makefile",
    ".cmake": "CMake",
    ".hack": "Hack", ".hh": "Hack",
    ".groovy": "Groovy",
}

OPENSOURCE_LICENSE_FILES = {
    "license", "license.txt", "license.md", "license.rst",
    "licence", "licence.txt", "licence.md",
    "copying", "copying.txt", "copying.md",
}

OPENSOURCE_SPDX_INDICATORS = [
    "mit license", "apache license", "gnu general public",
    "bsd license", "bsd 3-clause", "bsd 2-clause", "mozilla public",
    "isc license", "creative commons", "the unlicense", "eclipse public",
    "european union public", "common development",
]

SKIP_DIRS = {
    '.git', 'node_modules', 'vendor', 'vendors', '__pycache__', 'env', 'venv', '.venv',
    '.tox', 'build', 'dist', '.idea', '.vscode',
    '.next', '.nuxt', '.svelte-kit', '.output', '.cache', '.parcel-cache',
    'out', 'coverage', '.pytest_cache', '.mypy_cache',
    '.env', 'virtualenv', 'conda-env', 'site-packages', 'dist-packages',
    '.yarn', '.pnp', 'bower_components', 'jspm_packages', 'web_modules',
    'migrations', 'alembic',
    'test', 'tests', 'spec', 'specs', 'docs', 'documentation',
    'examples', 'samples', 'demo', 'benchmarks', 'screenshots',
    'Lib', 'lib64', 'Scripts', 'bin', 'Include', 'obj',
    'third_party', 'third-party', 'extern', 'external', 'custom_node_modules'
}

SKIP_EXTENSIONS = {
    '.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib', '.gitignore', '.gitmodules',
    '.zip', '.tar', '.gz', '.jpg', '.jpeg', '.png', '.gif',
    '.pdf', '.mp4', '.mp3', '.ttf', '.woff', '.woff2',
    '.eot', '.otf', '.ico', '.svg', '.webp', '.bmp',
    '.json', '.md', '.markdown', '.txt', '.yaml', '.yml', '.toml', '.xml', '.csv', '.tsv',
    '.diff', '.patch',
    '.org', '.rc', '.pro', '.properties', '.gradle', '.pom'
}

C_EXTENSIONS = {".c", ".h"}
CPP_EXTENSIONS = {".cpp", ".hpp", ".cc", ".cxx", ".c++", ".hxx", ".h++", ".C", ".H"}
PYTHON_EXTENSIONS = {".py", ".pyw", ".pyx", ".pxd", ".pyi"}
JAVASCRIPT_EXTENSIONS = {".js", ".mjs", ".cjs"}
TYPESCRIPT_EXTENSIONS = {".ts", ".tsx"}
JSX_EXTENSIONS = {".jsx"}
FRONTEND_FRAMEWORK_EXTENSIONS = {".vue", ".svelte"}
JAVA_EXTENSIONS = {".java"}
KOTLIN_EXTENSIONS = {".kt", ".kts"}
SCALA_EXTENSIONS = {".scala"}
GROOVY_EXTENSIONS = {".groovy", ".gradle"}
CLOJURE_EXTENSIONS = {".clj", ".cljs", ".cljc", ".edn"}
CSHARP_EXTENSIONS = {".cs"}
FSHARP_EXTENSIONS = {".fs", ".fsx", ".fsi"}
GO_EXTENSIONS = {".go"}
RUST_EXTENSIONS = {".rs"}
SWIFT_EXTENSIONS = {".swift"}
OBJC_EXTENSIONS = {".m", ".mm"}
UNIX_SHELL_EXTENSIONS = {".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh"}
WINDOWS_SHELL_EXTENSIONS = {".bat", ".cmd", ".ps1"}
RUBY_EXTENSIONS = {".rb", ".erb", ".rake"}
PHP_EXTENSIONS = {".php", ".php3", ".php4", ".php5", ".phtml"}
PERL_EXTENSIONS = {".pl", ".pm", ".t", ".pod"}
LUA_EXTENSIONS = {".lua"}
R_EXTENSIONS = {".r", ".R"}
HASKELL_EXTENSIONS = {".hs", ".lhs"}
OCAML_EXTENSIONS = {".ml", ".mli"}
ERLANG_EXTENSIONS = {".erl", ".hrl"}
ELIXIR_EXTENSIONS = {".ex", ".exs"}
ELM_EXTENSIONS = {".elm"}
DART_EXTENSIONS = {".dart"}
JULIA_EXTENSIONS = {".jl"}
NIM_EXTENSIONS = {".nim"}
CRYSTAL_EXTENSIONS = {".cr"}
ZIG_EXTENSIONS = {".zig"}
V_EXTENSIONS = {".v"}
VERILOG_EXTENSIONS = {".vh", ".sv"}
VHDL_EXTENSIONS = {".vhd", ".vhdl"}
ASSEMBLY_EXTENSIONS = {".asm", ".s", ".S"}
SOLIDITY_EXTENSIONS = {".sol"}
MOVE_EXTENSIONS = {".move"}
WEB_TEMPLATE_EXTENSIONS = {".jsp", ".asp", ".aspx"}
SQL_EXTENSIONS = {".sql", ".psql", ".mysql", ".pgsql"}
GRAPHQL_EXTENSIONS = {".graphql", ".gql"}
PROTOBUF_EXTENSIONS = {".proto"}
TERRAFORM_EXTENSIONS = {".tf", ".tfvars", ".hcl"}
SALT_EXTENSIONS = {".sls"}
JUPYTER_EXTENSIONS = {".ipynb"}
PASCAL_EXTENSIONS = {".pas", ".pp", ".inc"}
FORTRAN_EXTENSIONS = {".f", ".for", ".f90", ".f95", ".f03", ".f08"}
COBOL_EXTENSIONS = {".cob", ".cbl", ".cpy"}
ADA_EXTENSIONS = {".ada", ".adb", ".ads"}
D_EXTENSIONS = {".d"}
LISP_EXTENSIONS = {".lisp", ".lsp", ".cl", ".el"}
SCHEME_EXTENSIONS = {".scm", ".ss", ".rkt"}
VIM_EXTENSIONS = {".vim"}
TCL_EXTENSIONS = {".tcl"}
AWK_EXTENSIONS = {".awk"}
SED_EXTENSIONS = {".sed"}
MAKEFILE_EXTENSIONS = {".mk"}
CMAKE_EXTENSIONS = {".cmake"}
HACK_EXTENSIONS = {".hack", ".hh"}
REASON_EXTENSIONS = {".re", ".rei"}
PURESCRIPT_EXTENSIONS = {".purs"}

CODE_EXTENSIONS = (
    C_EXTENSIONS | CPP_EXTENSIONS | PYTHON_EXTENSIONS | JAVASCRIPT_EXTENSIONS |
    TYPESCRIPT_EXTENSIONS | JSX_EXTENSIONS | FRONTEND_FRAMEWORK_EXTENSIONS |
    JAVA_EXTENSIONS | KOTLIN_EXTENSIONS | SCALA_EXTENSIONS | GROOVY_EXTENSIONS |
    CLOJURE_EXTENSIONS | CSHARP_EXTENSIONS | FSHARP_EXTENSIONS | GO_EXTENSIONS |
    RUST_EXTENSIONS | SWIFT_EXTENSIONS | OBJC_EXTENSIONS | UNIX_SHELL_EXTENSIONS |
    WINDOWS_SHELL_EXTENSIONS | RUBY_EXTENSIONS | PHP_EXTENSIONS | PERL_EXTENSIONS |
    LUA_EXTENSIONS | R_EXTENSIONS | HASKELL_EXTENSIONS | OCAML_EXTENSIONS |
    ERLANG_EXTENSIONS | ELIXIR_EXTENSIONS | ELM_EXTENSIONS | DART_EXTENSIONS |
    JULIA_EXTENSIONS | NIM_EXTENSIONS | CRYSTAL_EXTENSIONS | ZIG_EXTENSIONS |
    V_EXTENSIONS | VERILOG_EXTENSIONS | VHDL_EXTENSIONS | ASSEMBLY_EXTENSIONS |
    SOLIDITY_EXTENSIONS | MOVE_EXTENSIONS | WEB_TEMPLATE_EXTENSIONS |
    SQL_EXTENSIONS | GRAPHQL_EXTENSIONS | PROTOBUF_EXTENSIONS |
    TERRAFORM_EXTENSIONS | SALT_EXTENSIONS |
    JUPYTER_EXTENSIONS | PASCAL_EXTENSIONS | FORTRAN_EXTENSIONS |
    COBOL_EXTENSIONS | ADA_EXTENSIONS | D_EXTENSIONS | LISP_EXTENSIONS |
    SCHEME_EXTENSIONS | VIM_EXTENSIONS | TCL_EXTENSIONS | AWK_EXTENSIONS |
    SED_EXTENSIONS | MAKEFILE_EXTENSIONS | CMAKE_EXTENSIONS | HACK_EXTENSIONS |
    REASON_EXTENSIONS | PURESCRIPT_EXTENSIONS
)

NON_CODE_EXTENSIONS = {
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".tex",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".csv", ".tsv", ".dat",
    ".log", ".tmp", ".temp", ".bak", ".swp", ".swo",
    ".lock", ".pem", ".crt", ".key", ".cer",
    ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"
}

SPECIAL_CODE_FILES = {
    "dockerfile": "code", "makefile": "code", "gnumakefile": "code",
    "rakefile": "code", "gemfile": "code", "vagrantfile": "code",
    "jenkinsfile": "code", "podfile": "code", "brewfile": "code",
    "cakefile": "code", "guardfile": "code", "fastfile": "code",
    "dangerfile": "code"
}

EXCLUDED_SPECIAL_FILES = {
    "readme", "license", "licence", "changelog", "changes",
    "authors", "contributors", "notice", "patents", "copying",
    "todo", "history", "news", "thanks", "credits", "citation"
}

CONFIG_FILENAMES = {
    'requirements.txt', 'package.json', 'pom.xml',
    'build.gradle', 'cargo.toml', 'dockerfile', 'makefile', 'setup.py',
    'gemfile', 'rakefile', 'podfile'
}

CI_FILENAMES = {
    ".gitlab-ci.yml", ".gitlab-ci.yaml", "circle.yml", ".circleci",
    "azure-pipelines.yml", "azure-pipelines.yaml", ".travis.yml",
    "appveyor.yml", "jenkinsfile"
}

CI_DIRECTORIES = {".github/workflows", ".circleci", ".azure-pipelines"}

CI_PATH_PATTERNS = {'.github/workflows', '.github\\workflows', '.circleci/', '.gitlab-ci', 'azure-pipelines'}

_overlap = CODE_EXTENSIONS & NON_CODE_EXTENSIONS
if _overlap:
    raise ValueError(
        f"CRITICAL: Extension overlap detected between CODE and NON_CODE: {_overlap}\n"
        f"This indicates a configuration error. Please review extension definitions."
    )

allCodeExts = []

extSets = [
    C_EXTENSIONS, CPP_EXTENSIONS, PYTHON_EXTENSIONS, JAVASCRIPT_EXTENSIONS,
    TYPESCRIPT_EXTENSIONS, JSX_EXTENSIONS, FRONTEND_FRAMEWORK_EXTENSIONS,
    JAVA_EXTENSIONS, KOTLIN_EXTENSIONS, SCALA_EXTENSIONS, GROOVY_EXTENSIONS,
    CLOJURE_EXTENSIONS, CSHARP_EXTENSIONS, FSHARP_EXTENSIONS, GO_EXTENSIONS,
    RUST_EXTENSIONS, SWIFT_EXTENSIONS, OBJC_EXTENSIONS, UNIX_SHELL_EXTENSIONS,
    WINDOWS_SHELL_EXTENSIONS, RUBY_EXTENSIONS, PHP_EXTENSIONS, PERL_EXTENSIONS,
    LUA_EXTENSIONS, R_EXTENSIONS, HASKELL_EXTENSIONS, OCAML_EXTENSIONS,
    ERLANG_EXTENSIONS, ELIXIR_EXTENSIONS, ELM_EXTENSIONS, DART_EXTENSIONS,
    JULIA_EXTENSIONS, NIM_EXTENSIONS, CRYSTAL_EXTENSIONS, ZIG_EXTENSIONS,
    V_EXTENSIONS, VERILOG_EXTENSIONS, VHDL_EXTENSIONS, ASSEMBLY_EXTENSIONS,
    SOLIDITY_EXTENSIONS, MOVE_EXTENSIONS, WEB_TEMPLATE_EXTENSIONS,
    SQL_EXTENSIONS, GRAPHQL_EXTENSIONS, PROTOBUF_EXTENSIONS,
    TERRAFORM_EXTENSIONS, SALT_EXTENSIONS,
    JUPYTER_EXTENSIONS, PASCAL_EXTENSIONS, FORTRAN_EXTENSIONS,
    COBOL_EXTENSIONS, ADA_EXTENSIONS, D_EXTENSIONS, LISP_EXTENSIONS,
    SCHEME_EXTENSIONS, VIM_EXTENSIONS, TCL_EXTENSIONS, AWK_EXTENSIONS,
    SED_EXTENSIONS, MAKEFILE_EXTENSIONS, CMAKE_EXTENSIONS, HACK_EXTENSIONS,
    REASON_EXTENSIONS, PURESCRIPT_EXTENSIONS
]

for extGroup in extSets:
    allCodeExts.extend(extGroup)

if len(allCodeExts) != len(set(allCodeExts)):
    from collections import Counter
    duplicates = [ext for ext, count in Counter(allCodeExts).items() if count > 1]
    raise ValueError(
        f"CRITICAL: Duplicate extensions found in CODE_EXTENSIONS groups: {duplicates}\n"
        f"Each extension should appear in only one group."
    )


def getWorkerCount() -> int:
    try:
        cpuCores = multiprocessing.cpu_count()
        return max(1, min(cpuCores, cpuCores - 1))
    except Exception:
        return 4


def getAdaptiveBatchSize(fileList: List[str]) -> int:
    try:
        if HAS_PSUTIL:
            availableMb = psutil.virtual_memory().available / (1024 * 1024)
        else:
            availableMb = 4096  # Assume 4GB if psutil missing
        safeFiles = int((availableMb * 0.80 * 1024) / 50)
        return max(50, min(safeFiles, len(fileList)))
    except Exception:
        return min(200, len(fileList))


def logResourceStats(label: str = "") -> None:
    """Log current CPU and RAM usage statistics."""
    if not HAS_PSUTIL:
        return
    try:
        cpuPct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        logger.info(
            f"[RESOURCES]{' ' + label if label else ''} "
            f"CPU={cpuPct:.1f}%  RAM={mem.percent:.1f}%  "
            f"Available={mem.available // (1024*1024)} MB"
        )
    except Exception:
        pass


def countLexicalTokens(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[A-Za-z_]+|\d+|==|!=|<=|>=|[^\s]", text)


def getLlmTokens(text: str) -> int:
    if not text:
        return 0

    if HAS_TIKTOKEN:
        try:
            return len(ENCODING.encode(text, disallowed_special=()))
        except Exception as e:
            logger.warning(f"[getLlmTokens] tiktoken encode failed: {e}")
    return max(1, len(text) // 4)


def countLoc(text: str) -> int:
    if not text:
        return 0

    return sum(1 for line in text.splitlines() if line.strip())


def computeSimilarity(tokens1: List[str], tokens2: List[str]) -> float:
    set1, set2 = set(tokens1), set(tokens2)
    if not set1 and not set2:
        return 1.0

    if not set1 or not set2:
        return 0.0

    return len(set1 & set2) / len(set1 | set2)


def cloneRepo(repoUrl: str, targetDir: str, githubToken: Optional[str] = None,
              gitlabToken: Optional[str] = None, bitbucketToken: Optional[str] = None) -> bool:
    actualUrl = repoUrl
    maskedUrl = repoUrl
    tokens_to_mask = []

    # Check for GitLab
    glInfo = extractGitLabRepoInfo(targetDir, repoUrl)
    if glInfo and gitlabToken:
        domain, project_path, _ = glInfo
        actualUrl = f"https://oauth2:{gitlabToken}@{domain}/{project_path}.git"
        maskedUrl = f"https://oauth2:[MASKED]@{domain}/{project_path}.git"
        tokens_to_mask.append(gitlabToken)
    else:
        # Check for Bitbucket
        bbInfo = extractBitbucketRepoInfo(targetDir, repoUrl)
        if bbInfo and bitbucketToken:
            domain, workspace, repo, _is_cloud = bbInfo
            actualUrl = f"https://x-token-auth:{bitbucketToken}@{domain}/{workspace}/{repo}.git"
            maskedUrl = f"https://x-token-auth:[MASKED]@{domain}/{workspace}/{repo}.git"
            tokens_to_mask.append(bitbucketToken)
        else:
            # Check for GitHub
            ghInfo = extractGitHubRepoInfo(targetDir, repoUrl)
            if ghInfo and githubToken:
                owner, repo = ghInfo
                actualUrl = f"https://{githubToken}@github.com/{owner}/{repo}.git"
                maskedUrl = f"https://[MASKED]@github.com/{owner}/{repo}.git"
                tokens_to_mask.append(githubToken)

    def mask_text(text: str) -> str:
        if not text:
            return text
        for t in tokens_to_mask:
            if t:
                text = text.replace(t, "[MASKED]")
        return text

    if actualUrl != repoUrl:
        logger.info(f"[cloneRepo] Rewrote URL for authenticated clone: {maskedUrl}")
        try:
            process = subprocess.Popen(
                ["git", "clone", "--progress", actualUrl, targetDir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            
            def stream_reader(pipe, dest):
                try:
                    for line in pipe:
                        dest.write(mask_text(line))
                        dest.flush()
                except Exception:
                    pass

            t_out = threading.Thread(target=stream_reader, args=(process.stdout, sys.stdout))
            t_err = threading.Thread(target=stream_reader, args=(process.stderr, sys.stderr))
            t_out.start()
            t_err.start()

            process.wait()
            t_out.join()
            t_err.join()

            return process.returncode == 0
        except FileNotFoundError:
            logger.error("[cloneRepo] 'git' command not found. Please install git.")
            return False
        except Exception as e:
            logger.error(f"[cloneRepo] Unexpected error during authenticated clone: {mask_text(str(e))}")
            return False
    else:
        try:
            result = subprocess.run(
                ["git", "clone", "--progress", actualUrl, targetDir],
                stdout=sys.stdout,
                stderr=sys.stderr,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            return result.returncode == 0
        except subprocess.CalledProcessError as e:
            logger.error(f"[cloneRepo] Clone failed with exit code {e.returncode}")
            return False
        except FileNotFoundError:
            logger.error("[cloneRepo] 'git' command not found. Please install git.")
            return False
        except Exception as e:
            logger.error(f"[cloneRepo] Unexpected error: {e}")
            return False


def isBinary(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
            return b"\0" in chunk
    except Exception:
        return True


def hashFile(filepath: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return f"hash_error_{filepath}"


def readFileSafe(filepath: str) -> Optional[str]:
    """Read a file safely using utf-8 or latin-1 fallback."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='latin-1') as f:
                return f.read()
        except Exception:
            return None
    except Exception:
        return None


def classifyFile(filename: str, ext: str) -> Tuple[bool, str]:
    """Determine if a file is code or non-code based on extensions."""
    nameLower = filename.lower()

    if nameLower in EXCLUDED_SPECIAL_FILES:
        return False, "excluded"

    if ext in NON_CODE_EXTENSIONS:
        return False, "non-code"

    if ext in CODE_EXTENSIONS:
        return True, "code"

    if not ext and nameLower in SPECIAL_CODE_FILES:
        return True, "code"

    return False, "unknown"


def runStage1Analysis(rootDir: str, maxFiles: Optional[int] = None) -> Tuple[Dict[str, Any], List[str]]:
    """
    Perform directory traversal to extract structure and detect repo health signals.
    Single-threaded I/O walk optimized for speed.
    """
    structure = {"dirs": 0, "files": 0, "extensions": defaultdict(int)}
    signals = {"hasSource": False, "hasTests": False, "hasConfig": False, "hasCI": False}
    fileList = []

    for dirpath, dirnames, filenames in os.walk(rootDir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]

        relDir = os.path.relpath(dirpath, rootDir)
        if relDir != ".":
            structure["dirs"] += 1

        relPathNormalized = relDir.replace('\\', '/').lower()
        for ciDir in CI_DIRECTORIES:
            if ciDir in relPathNormalized:
                signals["hasCI"] = True
                break

        for file in filenames:
            ext = os.path.splitext(file)[1].lower()
            basename = os.path.basename(file)
            basenameLower = basename.lower()

            if ext in SKIP_EXTENSIONS or (file.startswith('.') and ext):
                continue

            filepath = os.path.join(dirpath, file)

            if isVendoredOrGenerated(filepath, file):
                continue

            try:
                if os.path.getsize(filepath) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue

            isCode, classification = classifyFile(basename, ext)

            if classification == "excluded":
                continue

            structure["files"] += 1
            structure["extensions"][ext if ext else f".{classification}"] += 1
            fileList.append(filepath)

            if not signals["hasSource"] and isCode:
                signals["hasSource"] = True

            lname = file.lower()
            lpath = filepath.lower().replace('\\', '/')

            if 'test' in lname or 'spec' in lname or '/tests/' in lpath:
                signals["hasTests"] = True

            if lname in CONFIG_FILENAMES:
                signals["hasConfig"] = True

            if not signals["hasCI"]:
                if basenameLower in CI_FILENAMES:
                    signals["hasCI"] = True
                elif any(ciPath in lpath for ciPath in CI_PATH_PATTERNS):
                    signals["hasCI"] = True

            if maxFiles and structure["files"] >= maxFiles:
                break

        if maxFiles and structure["files"] >= maxFiles:
            break

    structure["extensions"] = dict(structure["extensions"])
    structure["test_file_count"] = sum(
        1 for fp in fileList
        if "test" in os.path.basename(fp).lower() or "spec" in os.path.basename(fp).lower()
    )
    fileCount = structure["files"]
    confidence = "high" if fileCount > 50 else "medium" if fileCount > 10 else "low"

    return {
        "structure": structure,
        "signals": signals,
        "confidence": confidence
    }, fileList


def getClocCommand() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ext = ".exe" if sys.platform == "win32" else ""
    local_cloc = os.path.join(script_dir, f"cloc{ext}")

    if os.path.isfile(local_cloc):
        return local_cloc
    return "cloc"


def runCloc(repoDir: str, fileList: List[str] = None) -> Dict[str, Any]:
    """
    Run cloc on the repository and return the parsed JSON output.
    If fileList is provided, it uses --list-file to ensure perfect sync.
    """
    try:
        cloc_cmd = getClocCommand()
        cmd = [cloc_cmd, "--json", "--quiet"]

        if fileList:
            # Create a temporary file list for cloc
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
                for f in fileList:
                    tmp.write(f + "\n")
                tmp_path = tmp.name

            cmd.append(f"--list-file={tmp_path}")
            result = subprocess.run(cmd, cwd=repoDir, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        else:
            # Fallback to directory scan if no list provided
            final_skip_dirs = set(SKIP_DIRS)
            for root, dirs, _ in os.walk(repoDir):
                if any(sd in root for sd in SKIP_DIRS):
                    dirs[:] = []
                    continue
                for d in list(dirs):
                    candidate = os.path.join(root, d)
                    is_venv_cfg = os.path.isfile(os.path.join(candidate, "pyvenv.cfg"))
                    has_site_pkgs = os.path.isdir(os.path.join(candidate, "Lib", "site-packages"))
                    if is_venv_cfg or has_site_pkgs:
                        final_skip_dirs.add(d)
                        dirs.remove(d)

            if final_skip_dirs:
                cmd.append(f"--exclude-dir={','.join(final_skip_dirs)}")
            if SKIP_EXTENSIONS:
                clean_exts = [ext.lstrip('.') for ext in SKIP_EXTENSIONS]
                cmd.append(f"--exclude-ext={','.join(clean_exts)}")
            cmd.append(".")
            result = subprocess.run(cmd, cwd=repoDir, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace")

        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"[runCloc] Failed to run cloc: {e}")
    return {}


def processFileBatch(batch: List[str]) -> List[Dict[str, Any]]:
    """
    Worker function to process file batch in parallel.
    Computes LOC, tokens, and prepares data for duplication analysis.
    Only code files contribute to token metrics.
    """
    results = []
    for filepath in batch:
        ext = os.path.splitext(filepath)[1].lower()
        basename = os.path.basename(filepath)

        isCode, classification = classifyFile(basename, ext)

        if not isCode:
            continue

        if isBinary(filepath):
            continue

        try:
            if os.path.getsize(filepath) > MAX_FILE_BYTES:
                continue

            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if not content:
                continue

            llmTokens = len(ENCODING.encode(content)) if HAS_TIKTOKEN else (len(content) // 4)
            lexicalTokens = len(countLexicalTokens(content))
            loc = countLoc(content)

            tokenList = countLexicalTokens(content) if (10 < lexicalTokens < 5000) else []

            # Framework detection via imports (first 100 lines)
            foundFws = []
            if ext.lstrip('.') in IMPORT_FRAMEWORK_MAP:
                lines = content.splitlines()[:100]
                for line in lines:
                    lineLower = line.lower()
                    if "import " in lineLower or "from " in lineLower or "require(" in lineLower:
                        for kw, formalName in IMPORT_FRAMEWORK_MAP[ext.lstrip('.')].items():
                            if re.search(rf"\b{kw}\b", lineLower):
                                foundFws.append(formalName)
            results.append({
                "filepath": filepath,
                "llm_tokens": llmTokens,
                "lexical_tokens": lexicalTokens,
                "loc": loc,
                "frameworks": list(set(foundFws)),
                "hash": hashFile(filepath),
                "token_set": tokenList
            })
        except Exception:
            continue

    return results


def runStage2Analysis(
    fileList: List[str]
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, List[str]]]:
    """
    Execute parallel deep analysis to compute total/per-file metrics,
    exact duplication (hash-based), and similarity-based duplication (Jaccard clustering).
    Uses ProcessPoolExecutor for CPU-bound tokenization.
    """
    if not fileList:
        return {}, {}, {}, {}

    numWorkers = getWorkerCount()
    batchSize = getAdaptiveBatchSize(fileList)
    batches = [fileList[i:i + batchSize] for i in range(0, len(fileList), batchSize)]

    logger.info(f"[stage2] Workers={numWorkers}  Batch={batchSize}  Files={len(fileList)}")

    perFileLlm = {}
    perFileLexical = {}
    perFileLoc = {}
    totalLlm = 0
    totalLexical = 0
    totalLoc = 0
    contentHashes = defaultdict(list)
    fileTokensMap = {}
    fileStatsMap = {}
    frameworkFindings = defaultdict(set)
    completedFiles = 0
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=numWorkers) as executor:
        futures = {executor.submit(processFileBatch, batch): batch for batch in batches}

        for future in as_completed(futures):
            try:
                batchResults = future.result()
            except Exception:
                continue

            for item in batchResults:
                fp = item["filepath"]
                llm = item["llm_tokens"]
                lex = item["lexical_tokens"]
                loc = item["loc"]
                h = item["hash"]
                ts = item["token_set"]
                fws = item.get("frameworks", [])

                totalLlm += llm
                totalLexical += lex
                totalLoc += loc
                perFileLlm[fp] = llm
                perFileLexical[fp] = lex
                perFileLoc[fp] = loc
                fileStatsMap[fp] = {"loc": loc, "llm_tokens": llm, "hash": h}
                contentHashes[h].append(fp)

                for fw in fws:
                    frameworkFindings["code_imports"].add(fw)

                if loc >= MIN_LOC and ts and llm >= MIN_TOKENS:
                    fileTokensMap[fp] = ts

                completedFiles += 1

            elapsed = time.time() - t0
            throughput = completedFiles / elapsed if elapsed > 0 else 0
            if completedFiles % 200 == 0:
                logger.info(f"  → Processed {completedFiles}/{len(fileList)} files  ({throughput:.1f}/s)")

    exactDuplicateTokens = 0
    exactDuplicateCount = 0
    exactDuplicatedFiles = set()

    for h, paths in contentHashes.items():
        if len(paths) > 1:
            exactDuplicateCount += (len(paths) - 1)
            for dupFile in paths[1:]:
                exactDuplicateTokens += perFileLlm.get(dupFile, 0)
                exactDuplicatedFiles.add(dupFile)

    adj = defaultdict(set)
    processedPairs = 0

    sizeBuckets = defaultdict(list)
    for fp, tokens in fileTokensMap.items():
        sizeBuckets[len(tokens) // 50].append(fp)

    for key in sizeBuckets:
        if len(sizeBuckets[key]) > MAX_BUCKET_COMPARE:
            logger.warning(f"[similarity] Pruning bucket of size {len(sizeBuckets[key])} to {MAX_BUCKET_COMPARE}")
            sizeBuckets[key] = sizeBuckets[key][:MAX_BUCKET_COMPARE]

    for bucketFiles in sizeBuckets.values():
        for i in range(len(bucketFiles)):
            if processedPairs >= MAX_SIMILARITY_PAIRS:
                break

            f1 = bucketFiles[i]
            for j in range(i + 1, len(bucketFiles)):
                if processedPairs >= MAX_SIMILARITY_PAIRS:
                    break

                f2 = bucketFiles[j]
                processedPairs += 1
                if computeSimilarity(fileTokensMap[f1], fileTokensMap[f2]) >= SIMILARITY_THRESHOLD:
                    adj[f1].add(f2)
                    adj[f2].add(f1)

    visited = set()
    similarClusters = []
    similarDuplicateTokens = 0
    seen = set()

    nodes = list(adj.keys())
    for node in nodes:
        if node not in visited:
            stack = [node]
            cluster = []
            while stack:
                curr = stack.pop()
                if curr in visited:
                    continue

                visited.add(curr)
                cluster.append(curr)
                if curr in adj:
                    stack.extend(adj[curr] - visited)

            if len(cluster) > 1:
                similarClusters.append(cluster)
                for file in cluster[1:]:
                    if file not in seen:
                        similarDuplicateTokens += perFileLlm.get(file, 0)
                        seen.add(file)

    totalDuplicateTokens = exactDuplicateTokens + similarDuplicateTokens
    tokenWeighted = (totalDuplicateTokens / totalLlm) if totalLlm > 0 else 0

    if len(similarClusters) > 0 and totalDuplicateTokens == 0:
        raise ValueError("[stage2] Duplication broken: similarity exists but duplicate_tokens = 0")

    if exactDuplicateCount > 0 and exactDuplicateTokens == 0:
        raise ValueError("[stage2] Exact duplicates not contributing to token count")

    if len(similarClusters) > 0 and similarDuplicateTokens == 0 and exactDuplicateTokens == 0:
        raise ValueError("[stage2] Inconsistent similarity vs duplication metrics")

    return {
        "metrics": {
            "llm_tokens": {"total": totalLlm, "per_file": perFileLlm},
            "lexical_tokens": {"total": totalLexical, "per_file": perFileLexical},
            "loc": {"total": totalLoc, "per_file": perFileLoc}
        },
        "ratios": {
            "tokens_per_loc": round(totalLlm / totalLoc if totalLoc else 0, 2),
            "lexical_to_llm": round(totalLexical / totalLlm if totalLlm else 0, 4)
        },
        "duplication_metrics": {
            "token_weighted": round(tokenWeighted, 4),
            "duplicate_tokens": totalDuplicateTokens,
            "exact_duplicate_files": exactDuplicateCount,
            "similar_clusters": similarClusters
        },
        "file_stats_summary": {"total_files_analyzed": len(fileStatsMap)}
    }, fileStatsMap, fileTokensMap, {k: list(v) for k, v in frameworkFindings.items()}


def calculateMeanStd(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, variance ** 0.5


def computeUniformity(fileStats: Dict[str, Any]) -> float:
    tokens = sorted([v["llm_tokens"] for v in fileStats.values() if v["llm_tokens"] > 0])
    if not tokens or len(tokens) < 2:
        return 0.0

    cutoff = int(len(tokens) * 0.90)
    tokens = tokens[:cutoff] if cutoff > 0 else tokens
    if len(tokens) < 2:
        return 0.0

    mean, std = calculateMeanStd(tokens)
    return 1.0 / (1.0 + (std / mean)) if mean > 0 else 0.0


def normalizeWeight(tokens: int) -> float:
    return min(1.0, tokens / 1000)


def calculateNamingConsistency(filePaths: List[str]) -> float:
    if not filePaths:
        return 0.0

    snake = sum(1 for f in filePaths if "_" in os.path.basename(f))
    total = len(filePaths)
    return snake / total


def calculateTokenSmoothness(fileStats: Dict[str, Any]) -> float:
    tokens = [v["llm_tokens"] for v in fileStats.values() if v["llm_tokens"] > 0]
    if not tokens or len(tokens) < 2:
        return 0.0

    mean, std = calculateMeanStd(tokens)
    cv = std / (mean + 1e-9)
    return 1.0 / (1.0 + cv)


def runAiDetectionAnalysis(
    fileStats: Dict[str, Any],
    duplicationMetrics: Dict[str, Any],
    fileTokensMap: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Execute AI-generated code detection using multi-signal heuristics.
    Weighted combination: duplication (30%), similarity (20%), cluster density (25%),
    uniformity (15%), naming (10%).
    """
    totalFiles = len(fileStats)
    if totalFiles == 0:
        return {"repo_score": 0.0, "confidence": "none", "signals": {}}

    filteredStats = {
        f: stats for f, stats in fileStats.items()
        if stats["loc"] >= MIN_LOC and stats["llm_tokens"] >= MIN_TOKENS
    }

    uniformity = computeUniformity(filteredStats)

    totalTokens = sum(s["llm_tokens"] for s in fileStats.values())
    dupTokens = duplicationMetrics.get("duplicate_tokens", 0)
    duplicationSignal = min(1.0, (dupTokens / totalTokens) * 2.5) if totalTokens > 0 else 0.0

    similarClusters = duplicationMetrics.get("similar_clusters", [])
    similarFiles = sum(len(cluster) for cluster in similarClusters)
    similaritySignal = similarFiles / totalFiles if totalFiles > 0 else 0.0

    clusterDensity = max(
        (len(cluster) / totalFiles) for cluster in similarClusters
    ) if similarClusters else 0.0

    naming = calculateNamingConsistency(list(filteredStats.keys()))
    smoothness = calculateTokenSmoothness(filteredStats)

    if len(similarClusters) > 0 and duplicationMetrics.get("duplicate_tokens", 0) == 0:
        raise ValueError("[aiDetection] CRITICAL: similarity exists but duplicate_tokens = 0")

    if duplicationMetrics.get("exact_duplicate_files", 0) > 0 and duplicationMetrics.get("duplicate_tokens", 0) == 0:
        raise ValueError("[aiDetection] CRITICAL: exact duplicates not contributing to tokens")

    if similaritySignal > 0.8 and duplicationSignal < 0.1:
        raise ValueError("[aiDetection] CRITICAL: inconsistent similarity vs duplication")

    aiScore = (
        0.30 * duplicationSignal +
        0.20 * similaritySignal +
        0.25 * clusterDensity +
        0.15 * uniformity +
        0.10 * naming
    )
    aiScore = max(0.0, min(1.0, aiScore))

    perFileScores = {}
    for file, stats in filteredStats.items():
        weight = normalizeWeight(stats["llm_tokens"])
        isDuplicated = any(file in cluster for cluster in similarClusters)
        perFileScores[file] = min(1.0, (weight * 0.6 + (0.4 if isDuplicated else 0.0)))

    confidence = "high" if len(filteredStats) > 50 else "medium" if len(filteredStats) > 10 else "low"

    return {
        "repo_score": round(aiScore, 4),
        "confidence": confidence,
        "signals": {
            "uniformity": round(uniformity, 4),
            "duplication": round(duplicationSignal, 4),
            "similarity": round(similaritySignal, 4),
            "cluster_density": round(clusterDensity, 4),
            "naming": round(naming, 4),
            "smoothness": round(smoothness, 4)
        },
        "per_file_scores": {f: round(s, 4) for f, s in perFileScores.items()}
    }


def runGit(args: List[str], cwd: str, timeout: int = GIT_LOG_TIMEOUT) -> Optional[str]:
    """Run a git command safely, returning stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def getAllContributors(repoDir: str) -> List[Dict[str, Any]]:
    """
    Return a structured list of every contributor discovered from git history.

    Uses ``git shortlog -sne --all`` which emits lines of the form:
        <commit_count>\\t<Name> <email>

    This is additive metadata only — it does NOT affect unique_contributors,
    commit_count, or any other existing Stage 0 field.

    Returns a list of dicts:
        [{"name": str, "email": str, "commits": int}, ...]
    sorted descending by commit count.
    On any failure returns an empty list (no crash, no pipeline impact).
    """
    # Use a dedicated timeout — shortlog on repos with 10k+ commits can be slow.
    # 300 s is generous; on failure we return [] safely, never crash.
    raw = runGit(["shortlog", "-sne", "--all"], repoDir, timeout=300)
    if not raw:
        return []

    contributors: List[Dict[str, Any]] = []
    # Regex: optional leading spaces, commit count, tab, display name + optional <email>
    lineRe = re.compile(
        r"^\s*(\d+)\s+(.+?)(?:\s+<([^>]*)>)?\s*$"
    )
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = lineRe.match(line)
        if not m:
            continue
        commitCountStr, rawName, rawEmail = m.group(1), m.group(2), m.group(3)

        # Trim whitespace only — no identity merging
        name = (rawName or "").strip() or ""
        email = (rawEmail or "").strip() or ""

        try:
            commits = int(commitCountStr)
        except (ValueError, TypeError):
            commits = 0

        contributors.append({"name": name, "email": email, "commits": commits})

    contributors.sort(key=lambda x: x["commits"], reverse=True)
    return contributors


def runStage0GitAnalysis(repoDir: str) -> Dict[str, Any]:
    """
    Stage 0 — Git Metadata Extraction.

    Collects without cloning: commit count, unique contributors, active
    development span, commit quality, and full-history integrity signals.
    Requires the directory to be a valid git repository.
    """
    result: Dict[str, Any] = {
        "available": False,
        "commit_count": 0,
        "unique_contributors": 0,
        "first_commit_date": None,
        "last_commit_date": None,
        "active_span_months": 0,
        "meaningful_commit_pct": 0.0,
        "history_integrity": {
            "appears_intact": False,
            "has_merge_commits": False,
            "force_push_risk": False,
            "single_bulk_commit": False,
            "notes": [],
        },
        "branch_count": 0,
        "default_branch": None,
    }

    # Quick sanity-check: is this a git repo?
    if not os.path.isdir(os.path.join(repoDir, ".git")):
        result["notes"] = "Not a git repository — Stage 0 skipped."
        return result

    result["available"] = True
    print(f"[Stage 0] Analyzing Git history in {repoDir}...")

    raw = runGit(["rev-list", "--count", "HEAD"], repoDir)
    if raw and raw.isdigit():
        result["commit_count"] = int(raw)

    raw = runGit(["log", "--all", "--format=%ae"], repoDir)
    if raw:
        emails = {e.strip().lower() for e in raw.splitlines() if e.strip()}
        result["unique_contributors"] = len(emails)

    tagsRaw = runGit(["tag"], repoDir)
    result["tag_count"] = len(tagsRaw.splitlines()) if tagsRaw else 0

    rawTs = runGit(["log", "--all", "--format=%at"], repoDir)
    if rawTs:
        tsList = []
        for line in rawTs.splitlines():
            t = line.strip()
            if t.isdigit():
                tsList.append(int(t))

        if tsList:
            firstDt = datetime.datetime.fromtimestamp(min(tsList))
            lastDt = datetime.datetime.fromtimestamp(max(tsList))

            result["first_commit_date"] = firstDt.strftime("%Y-%m-%d")
            result["last_commit_date"] = lastDt.strftime("%Y-%m-%d")
            result["last_update"] = lastDt.strftime("%Y-%m-%d")

            delta = lastDt - firstDt
            result["active_span_months"] = round(delta.days / 30.44, 1)

    rawMsgs = runGit(["log", "--all", "--format=%s"], repoDir)
    if rawMsgs:
        msgs = [m.strip() for m in rawMsgs.splitlines() if m.strip()]
        trivial = re.compile(
            r"^(wip|fix|fixup!|squash!|update|commit|temp|tmp|test|merge|revert|bump)[\s\.\!]*$",
            re.IGNORECASE,
        )
        meaningful = sum(
            1 for m in msgs if len(m) > 10 and not trivial.match(m)
        )
        result["meaningful_commit_pct"] = round(
            meaningful / len(msgs) * 100 if msgs else 0, 1
        )
        result["total_commits_sampled"] = len(msgs)

    rawMerge = runGit(["log", "--all", "--merges", "--format=%H"], repoDir)
    if rawMerge:
        result["history_integrity"]["has_merge_commits"] = bool(rawMerge.strip())
        result["history_integrity"]["merge_commit_count"] = len(
            [h for h in rawMerge.splitlines() if h.strip()]
        )

    rawNonmerge = runGit(["log", "--all", "--no-merges", "--format=%s"], repoDir)
    result["meaningful_commit_count"] = 0
    if rawNonmerge:
        nonmergeMsgs = [m.strip() for m in rawNonmerge.splitlines() if m.strip()]
        trivial = re.compile(
            r"^(wip|fix|fixup!|squash!|update|commit|temp|tmp|test|merge|revert|bump)[\s\.\!]*$",
            re.IGNORECASE,
        )
        result["meaningful_commit_count"] = sum(
            1 for m in nonmergeMsgs if len(m) > 10 and not trivial.match(m)
        )

    if result["commit_count"] > 0:
        rawDates = runGit(["log", "--all", "--format=%ci"], repoDir)
        if rawDates:
            dates = [line[:10] for line in rawDates.splitlines() if line.strip()]
            uniqueDays = len(set(dates))
            if uniqueDays == 1 and result["commit_count"] > 10:
                result["history_integrity"]["single_bulk_commit"] = True
                result["history_integrity"]["notes"].append(
                    "All commits on a single day — possible bulk import."
                )

    shallowFile = os.path.join(repoDir, ".git", "shallow")
    if os.path.exists(shallowFile):
        result["history_integrity"]["force_push_risk"] = True
        result["history_integrity"]["notes"].append(
            "Shallow clone detected — full history may be truncated."
        )

    integrityOk = (
        not result["history_integrity"]["single_bulk_commit"]
        and not result["history_integrity"]["force_push_risk"]
        and result["commit_count"] >= 2
    )
    result["history_integrity"]["appears_intact"] = integrityOk

    result["all_contributors"] = getAllContributors(repoDir)

    rawBranches = runGit(["branch", "-a"], repoDir)
    if rawBranches:
        uniqueBranches = set()
        for line in rawBranches.splitlines():
            line = line.strip()
            if not line or "->" in line:
                continue
            if line.startswith("*"):
                line = line[1:].strip()
            prefixes = ["remotes/origin/", "origin/", "refs/heads/", "refs/remotes/origin/"]
            for prefix in prefixes:
                if line.startswith(prefix):
                    line = line[len(prefix):]
                    break
            if line:
                uniqueBranches.add(line)
        result["branch_count"] = len(uniqueBranches)

    rawHead = runGit(["symbolic-ref", "--short", "HEAD"], repoDir)
    if rawHead:
        result["default_branch"] = rawHead

    return result


def computeLanguageBreakdown(
    fileList: List[str], perFileLoc: Dict[str, int]
) -> Dict[str, Any]:
    """
    Map every analysed file to a display language name using EXT_TO_LANGUAGE.
    Returns per-language LOC counts, file counts, and percentage shares.
    Also detects special extensionless code files (Dockerfile, Makefile, etc.).
    """
    langLoc: Dict[str, int] = defaultdict(int)
    langFiles: Dict[str, int] = defaultdict(int)
    totalLoc = sum(perFileLoc.values())

    for fp in fileList:
        ext = os.path.splitext(fp)[1].lower()
        name = os.path.basename(fp).lower()
        if not ext and name in SPECIAL_CODE_FILES:
            lang = name.capitalize()
        else:
            lang = EXT_TO_LANGUAGE.get(ext) or EXT_TO_LANGUAGE.get(
                os.path.splitext(fp)[1]  # try original case for .R etc.
            )

        if not lang:
            continue

        loc = perFileLoc.get(fp, 0)
        langLoc[lang] += loc
        langFiles[lang] += 1

    breakdown = {}
    for lang, loc in sorted(langLoc.items(), key=lambda x: -x[1]):
        pct = round(loc / totalLoc * 100, 2) if total_loc else 0.0
        breakdown[lang] = {
            "loc":   loc,
            "files": langFiles[lang],
            "pct":   pct,
        }

    return {
        "languages":      list(breakdown.keys()),
        "language_count": len(breakdown),
        "breakdown":      breakdown,
    }


def detectFrameworks(repoDir: str) -> Dict[str, List[str]]:
    """
    Scan manifest files recursively for framework/library keywords.
    Aggregates results from all found manifests (useful for monorepos).
    """
    detected: Dict[str, List[str]] = {}

    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for manifest, keywords in FRAMEWORK_MANIFEST_PARSERS.items():
            if manifest in files:
                manifestPath = os.path.join(root, manifest)
                try:
                    with open(manifestPath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                    found = [kw for kw in keywords if kw in content]
                    if found:
                        if manifest not in detected:
                            detected[manifest] = []
                        detected[manifest].extend(found)
                except Exception:
                    continue
    for m in detected:
        detected[m] = list(set(detected[m]))
    return detected


def analyzeCodeAge(repoDir: str, fileList: List[str]) -> Dict[str, Any]:
    """
    Determine staleness of the codebase by querying the last-modified commit
    date for a sample of files via `git log --follow`.
    Returns the fraction of files not touched in the past 1 and 2 years,
    plus a breakdown bucket.
    """
    result: Dict[str, Any] = {
        "available": False,
        "sampled_files": 0,
        "pct_untouched_1yr": 0.0,
        "pct_untouched_2yr": 0.0,
        "age_distribution": {},
    }

    if not os.path.isdir(os.path.join(repoDir, ".git")):
        return result

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    buckets: Dict[str, int] = {
        "<6m": 0, "6m-1yr": 0, "1yr-2yr": 0, ">2yr": 0
    }
    sample = fileList[:500]
    ages: List[float] = []          # age in months

    for fp in sample:
        rel = os.path.relpath(fp, repoDir)
        raw = runGit(
            ["log", "--follow", "--format=%ci", "--max-count=1", "--", rel],
            repoDir,
            timeout=10,
        )
        if not raw:
            continue
        try:
            last_mod = datetime.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            months = (now - last_mod).days / 30.44
            ages.append(months)
            if months < 6:
                buckets["<6m"] += 1
            elif months < 12:
                buckets["6m-1yr"] += 1
            elif months < 24:
                buckets["1yr-2yr"] += 1
            else:
                buckets[">2yr"] += 1
        except Exception:
            continue

    if not ages:
        return result

    total = len(ages)
    old_1yr = sum(1 for a in ages if a > 12)
    old_2yr = sum(1 for a in ages if a > 24)

    result.update({
        "available": True,
        "sampled_files": total,
        "pct_untouched_1yr": round(old_1yr / total * 100, 1),
        "pct_untouched_2yr": round(old_2yr / total * 100, 1),
        "median_age_months": round(sorted(ages)[total // 2], 1),
        "age_distribution": buckets,
    })
    return result


def detectInfrastructure(repoDir: str) -> Dict[str, List[str]]:
    """Scan manifests and files for DBs, Deployment tools, and API usage recursively."""
    found = {"databases": set(), "deployment": set(), "apis": set()}

    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for m in INFRA_MANIFESTS_TO_CHECK:
            if m in files:
                path = os.path.join(root, m)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        for cat, keywords in INFRASTRUCTURE_INDICATORS.items():
                            for k in keywords:
                                if k in content:
                                    found[cat].add(k)

                        # Special Firebase Logic
                        if "firebase" in content:
                            found["apis"].add("firebase")
                            if any(x in content for x in ["firestore", "firebase-database", "realtime-database"]):
                                found["databases"].add("firebase")
                except Exception:
                    pass

        for cf in INFRA_CONFIG_FILES_TO_CHECK:
            if cf in files:
                path = os.path.join(root, cf)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        for prefix, db_name in INFRA_CONN_MAP.items():
                            if prefix in content:
                                found["databases"].add(db_name)
                except Exception:
                    pass

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in INFRA_DB_EXTENSIONS:
                found["databases"].add(f"Physical DB: {f}")

            if ext in INFRA_CODE_EXTENSIONS:
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as code_f:
                        head = "".join([code_f.readline() for _ in range(300)]).lower()

                        for cat, keywords in INFRASTRUCTURE_INDICATORS.items():
                            for k in keywords:
                                # Stricter regex: must be an import, require, or connection string
                                # Patterns: import k, from k, require('k'), k://, new k(
                                patterns = [
                                    rf"\bimport\s+{re.escape(k)}\b",
                                    rf"\bfrom\s+{re.escape(k)}\b",
                                    rf"require\(['\"]{re.escape(k)}['\"]",
                                    rf"\b{re.escape(k)}://",
                                    rf"\bnew\s+{re.escape(k)}\("
                                ]
                                if any(re.search(p, head) for p in patterns):
                                    found[cat].add(k)
                except Exception:
                    pass

        # 3. Detect via File Extensions (e.g. .sql files)
        if any(f.endswith(".sql") for f in files):
            found["databases"].add("SQL (Schema found)")

    # 2. Check for specific filenames

    for root, dirs, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue
        for d in dirs:
            if d == ".github":
                if os.path.exists(os.path.join(root, d, "workflows")):
                    found["deployment"].add("github actions")
        for f in files:
            f_lower = f.lower()
            for key, (cat, val) in INFRA_FILE_MAP.items():
                if key in f_lower:
                    found[cat].add(val)

    result = {}
    for cat, items in found.items():
        mapped = set()
        for item in items:
            # Map to canonical name if exists, otherwise title-case it
            name = INFRA_CANONICAL_MAP.get(item.lower(), item.title())
            mapped.add(name)

        # Deduplicate redundant ORM vs DB entries if both exist
        final_list = sorted(list(mapped))
        result[cat] = final_list

    return result


def isVendoredOrGenerated(filepath: str, filename: str) -> bool:
    """Detect vendored, minified, or generated files — mimics GitLab Linguist."""
    fnLower = filename.lower()
    fpLower = filepath.replace("\\", "/").lower()

    if fnLower in BOILERPLATE_FILES:
        return True

    # 1. Minified files
    if fnLower.endswith((".min.js", ".min.css", ".min.map")):
        return True

    # 2. Source maps
    if fnLower.endswith((".js.map", ".css.map")):
        return True

    # 3. Known vendored library names
    nameNoExt = os.path.splitext(fnLower)[0]
    # Strip version suffixes like "jquery-3.6.0" -> "jquery"
    nameBase = nameNoExt.split("-")[0].split(".")[0]
    if nameBase in VENDORED_NAMES or nameNoExt in VENDORED_NAMES:
        return True
    for vn in VENDORED_NAMES:
        if vn in fnLower:
            return True

    # 4. Vendored directories
    for vd in VENDORED_DIRS:
        if vd in fpLower:
            return True

    # 5. Bundle / chunk patterns (webpack, rollup, parcel output)
    if fnLower in BUNDLE_PATTERNS or any(p in fnLower for p in BUNDLE_PATTERNS):
        return True

    # 6. Large single-line files (machine-generated) — check first 4KB
    try:
        if os.path.getsize(filepath) > 1024 * 1024:  # > 1MB
            with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                sample = fh.read(4096)
                if sample and "\n" not in sample.strip() and len(sample) >= 4000:
                    return True
    except Exception:
        pass

    return False


def calculateByteBreakdown(repoDir: str) -> Dict[str, int]:
    """Calculate total bytes per language using GitLab Linguist-style rules.

    Excludes: minified files, vendored libraries, generated bundles,
    source maps, and machine-generated single-line files.
    """
    byte_counts = {}
    for root, dirs, files in os.walk(repoDir):
        # Prune skip directories and virtual environments
        def _is_venv_dir(d, parent):
            candidate = os.path.join(parent, d)
            return (os.path.isdir(os.path.join(candidate, "Lib", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.10", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.11", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.12", "site-packages")) or
                    os.path.isfile(os.path.join(candidate, "pyvenv.cfg")))

        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith('.')
                   and not _is_venv_dir(d, root)]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXT_TO_LANG_MAP:
                continue

            filepath = os.path.join(root, f)

            # Skip vendored / generated / minified
            if isVendoredOrGenerated(filepath, f):
                continue

            base_lang = EXT_TO_LANG_MAP[ext]
            identity = base_lang

            try:
                size = os.path.getsize(filepath)
                # Quick scan for identity (first 50 lines)
                if ext in {'.py', '.js', '.jsx', '.ts', '.tsx'}:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f_obj:
                        head = [f_obj.readline().lower() for _ in range(50)]

                    content_head = "".join(head)
                    found_id = None

                    # Framework mapping logic
                    if ext == '.py':
                        if 'django' in content_head:
                            found_id = "Django (Python)"
                        elif 'flask' in content_head:
                            found_id = "Flask (Python)"
                        elif 'fastapi' in content_head:
                            found_id = "FastAPI (Python)"
                        elif 'tornado' in content_head:
                            found_id = "Tornado (Python)"
                        elif 'aiohttp' in content_head:
                            found_id = "aiohttp (Python)"
                    elif ext in {'.js', '.jsx', '.ts', '.tsx'}:
                        if 'next' in content_head:
                            found_id = f"Next.js ({base_lang})"
                        elif 'react' in content_head:
                            found_id = f"React ({base_lang})"
                        elif 'vue' in content_head:
                            found_id = f"Vue ({base_lang})"
                        elif 'express' in content_head:
                            found_id = f"Express ({base_lang})"
                        elif 'jest' in content_head:
                            found_id = f"Jest ({base_lang})"

                    if found_id:
                        identity = found_id

                byte_counts[identity] = byte_counts.get(identity, 0) + size
            except Exception:
                continue
    return byte_counts


def analyzeDocumentation(repoDir: str) -> Dict[str, Any]:
    """Extract description and setup guidelines from README (recursive)."""
    res = {"description": "N/A", "has_setup": False, "doc_quality": "Low", "env_vars": "None"}

    readmePath = None
    # Scan up to depth 2 for README
    for root, _, files in os.walk(repoDir):
        depth = root.replace(repoDir, "").count(os.sep)
        if depth > 2:
            continue
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for f in files:
            if f.lower().startswith("readme"):
                readmePath = os.path.join(root, f)
                break
        if readmePath:
            break

    if readmePath:
        try:
            with open(readmePath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                lines = content.splitlines()

                # Simple description (first non-header paragraph)
                for line in lines:
                    clean = line.strip()
                    if clean and not clean.startswith("#"):
                        res["description"] = clean[:200] + ("..." if len(clean) > 200 else "")
                        break
                # Setup detection
                headers = [line.lower() for line in lines if line.strip().startswith("#")]
                if any(any(k in h for k in DOC_SETUP_KEYWORDS) for h in headers):
                    res["has_setup"] = True

                # Quality heuristic (based on number of headers and length)
                if len(headers) > 5 and len(content) > 2000:
                    res["doc_quality"] = "High"
                elif len(headers) > 2:
                    res["doc_quality"] = "Medium"

                # Env vars detection
                if ".env" in content.lower() or "environment variables" in content.lower():
                    res["env_vars"] = "Detected in README"
        except Exception:
            pass

    # Check for .env files
    for f in os.listdir(repoDir):
        if ".env" in f.lower():
            res["env_vars"] = "Mapped (.env file present)"

    return res


def detectCoverage(repoDir: str) -> str:
    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue
        for f in files:
            if f.lower() in COVERAGE_FILES:
                return "Detected"
    return "N/A"


def estimateTestCases(fileList: List[str]) -> int:
    """Heuristic count of test cases by scanning patterns in test files."""
    count = 0
    testFiles = [f for f in fileList if "test" in f.lower() or "spec" in f.lower()]

    for path in testFiles[:200]:  # Limit scan to first 200 test files for speed
        ext = path.split('.')[-1].lower()
        pattern = None
        if ext == "py":
            pattern = TEST_CASE_PATTERNS["python"]
        elif ext in ("js", "jsx"):
            pattern = TEST_CASE_PATTERNS["javascript"]
        elif ext in ("ts", "tsx"):
            pattern = TEST_CASE_PATTERNS["typescript"]
        elif ext == "java":
            pattern = TEST_CASE_PATTERNS["java"]
        elif ext == "go":
            pattern = TEST_CASE_PATTERNS["go"]
        elif ext == "php":
            pattern = TEST_CASE_PATTERNS["php"]
        elif ext == "rb":
            pattern = TEST_CASE_PATTERNS["ruby"]

        if pattern:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    count += len(pattern.findall(content))
            except Exception:
                pass
    return count


def computeComplexityScore(loc: int, total_tokens: int, file_count: int) -> str:
    if loc == 0:
        return "N/A"
    density = total_tokens / loc
    mod = loc / file_count if file_count > 0 else loc

    if density > 15 or mod > 500:
        return "High"
    if density > 8 or mod > 200:
        return "Moderate"
    return "Low"


def detectOpenSource(repoDir: str) -> Dict[str, Any]:
    """
    Detect whether a repository is open-source (recursive search).
    Checks for LICENSE files and known SPDX identifiers.
    """
    result = {
        "is_open_source": False,
        "license_file": None,
        "license_type": None,
        "has_readme": False,
    }

    for root, _, files in os.walk(repoDir):
        depth = root.replace(repoDir, "").count(os.sep)
        if depth > 2:
            continue
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for f in files:
            f_lower = f.lower()
            # README detection
            if f_lower.startswith("readme"):
                result["has_readme"] = True

            # LICENSE detection
            if f_lower in OPENSOURCE_LICENSE_FILES:
                result["license_file"] = f
                license_path = os.path.join(root, f)
                try:
                    with open(license_path, "r", encoding="utf-8", errors="ignore") as f_obj:
                        content = f_obj.read(2000).lower()
                    for indicator in OPENSOURCE_SPDX_INDICATORS:
                        if indicator in content:
                            result["is_open_source"] = True
                            result["license_type"] = indicator.title()
                            break
                    else:
                        result["is_open_source"] = True
                        result["license_type"] = "Unknown (license file present)"
                except Exception:
                    result["is_open_source"] = True

        if result["license_file"] and result["has_readme"]:
            break  # Found both, can stop early

    return result


def computeRepoRating(
    gitData: Dict[str, Any],
    stage1Signals: Dict[str, Any],
    metricsData: Dict[str, Any],
    langData: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stage 4 — Composite repository quality rating (0.0 – 10.0).

    Combines multiple signals with weighted criteria to produce an overall rating and label.
    Also checks if minimum thresholds are met for basic health.
    """
    signals = stage1Signals.get("signals", {})
    structure = stage1Signals.get("structure", {})
    loc = metricsData.get("loc", {}).get("value", 0) or metricsData.get("loc", {}).get("total", 0)
    scores: Dict[str, float] = {}

    def _score(value, crit_key: str) -> float:
        c = RATING_CRITERIA[crit_key]
        mn, pref = c["min"], c["preferred"]
        if value >= pref:
            return 1.0
        if value >= mn:
            return 0.5 + 0.5 * (value - mn) / max(pref - mn, 1)
        if value > 0:
            return 0.5 * value / max(mn, 1)
        return 0.0

    scores["commits"] = _score(gitData.get("commit_count", 0), "commits")
    scores["dev_span_months"] = _score(gitData.get("active_span_months", 0), "dev_span_months")
    scores["contributors"] = _score(gitData.get("unique_contributors", 0), "contributors")
    scores["loc"] = _score(loc, "loc")
    scores["has_tests"] = 1.0 if signals.get("hasTests") else 0.0
    scores["has_ci"] = 1.0 if signals.get("hasCI") else 0.0

    # Test file count approximation via stage1 structure (count test-named files)
    test_file_count = structure.get("test_file_count", 0)
    scores["test_file_count"] = _score(test_file_count, "test_file_count")
    # Source file ratio
    total_files = structure.get("files", 0)
    lang_files_total = sum(
        v["files"] for v in langData.get("breakdown", {}).values()
    )
    src_ratio = lang_files_total / total_files if total_files > 0 else 0.0
    scores["source_file_ratio"] = _score(src_ratio, "source_file_ratio")
    # Weighted total
    weighted = sum(
        scores[k] * RATING_CRITERIA[k]["weight"]
        for k in scores
    )
    rating_10 = round(weighted * 10, 2)

    label = (
        "Excellent" if rating_10 >= 8 else
        "Good" if rating_10 >= 6 else
        "Fair" if rating_10 >= 4 else
        "Poor"
    )
    return {
        "rating":       rating_10,
        "label":        label,
        "component_scores": {k: round(v, 4) for k, v in scores.items()},
        "meets_minimum": all([
            gitData.get("commit_count", 0) >= RATING_CRITERIA["commits"]["min"],
            gitData.get("unique_contributors", 0) >= RATING_CRITERIA["contributors"]["min"],
            loc >= RATING_CRITERIA["loc"]["min"],
            signals.get("hasTests", False),
        ]),
    }


def scanSecrets(fileList: List[str]) -> Dict[str, Any]:
    """
    Stage 5 — Basic credential / secret scanning.
    Uses regex patterns to detect potential secrets in text-based files.`
    """
    findings: List[Dict[str, str]] = []
    scanned = 0

    for fp in fileList:
        ext = os.path.splitext(fp)[1].lower()
        name = os.path.basename(fp).lower()

        # Only scan text-based source files likely to contain secrets
        if ext not in SECRET_SCAN_EXTENSIONS and name not in {".env", "secrets"}:
            if ext in SECRET_SCAN_SKIP or ext not in CODE_EXTENSIONS:
                continue

        try:
            if os.path.getsize(fp) > 500_000:   # skip files > 500KB
                continue
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        scanned += 1
        for secret_type, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({
                    "file": os.path.basename(fp),
                    "path": fp,
                    "type": secret_type,
                })
                break

    return {
        "files_scanned":   scanned,
        "findings_count":  len(findings),
        "findings":        findings,
        "clean":           len(findings) == 0,
        "risk_level": (
            "high" if len(findings) >= 5 else
            "medium" if len(findings) >= 1 else
            "low"
        ),
    }


def extractGitLabRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str, str]]:
    """
    Detect GitLab domain, project path, and project name from inputTarget URL or local git configuration.
    Returns: (domain, project_path, project_name)
    """
    import urllib.parse
    for candidate in [inputTarget, targetDir]:
        if not candidate:
            continue
            
        # 1. Handle HTTP/HTTPS URLs (including ones with embedded oauth2 tokens/passwords)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            try:
                parsed = urllib.parse.urlparse(candidate)
                hostname = parsed.hostname
                if hostname and "gitlab" in hostname.lower():
                    path = parsed.path.lstrip("/")
                    if path.endswith(".git"):
                        path = path[:-4]
                    name = path.split("/")[-1]
                    return hostname, path, name
            except Exception:
                pass
                
        # 2. Handle SSH and other Git URL formats (e.g. git@gitlab.com:org/repo.git)
        else:
            if "gitlab" in candidate.lower():
                if "@" in candidate:
                    parts = candidate.split("@", 1)[1]
                else:
                    parts = candidate
                
                m = re.split(r'[:/]', parts, 1)
                if len(m) == 2:
                    domain = m[0]
                    path = m[1]
                    if path.endswith(".git"):
                        path = path[:-4]
                    name = path.split("/")[-1]
                    return domain, path, name

    # Check git config remote origin
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            return extractGitLabRepoInfo(targetDir, remote_url)
    return None


def callGitLabApi(url: str, headers: dict, params: dict = None) -> Optional[Any]:
    """
    Executes a GitLab API request with rate limit handling and retries.
    """
    import requests
    retries = 0
    max_retries = 5
    backoff = 2
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (backoff ** retries)
                logger.warning(f"GitLab API rate limit hit. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"GitLab API server error ({response.status_code}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            else:
                logger.error(f"GitLab API request failed with status code {response.status_code}: {response.text}")
                return None
        except requests.RequestException as e:
            sleep_time = backoff ** retries
            logger.warning(f"Network error during GitLab API call: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    return None


def fetchGitLabMergeRequests(domain: str, project_path: str, token: Optional[str]) -> List[Dict[str, Any]]:
    """
    Fetch all Merge Requests for the repository using GitLab API, paginating.
    """
    import urllib.parse
    mr_list = []
    logger.info("Fetching GitLab Merge Requests...")

    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded_path = urllib.parse.quote_plus(project_path)
    url = f"https://{domain}/api/v4/projects/{encoded_path}/merge_requests"

    params = {
        "state": "all",
        "per_page": 100,
        "page": 1
    }

    while True:
        logger.info(f"Fetching GitLab MRs page {params['page']}...")
        response = callGitLabApi(url, headers, params)
        if not response:
            break

        data = response.json()
        if not data:
            break

        for mr in data:
            state = mr.get("state")
            mapped_state = "open" if state == "opened" else "closed"
            merged_at = mr.get("merged_at")

            mr_dict = {
                "number": mr.get("iid"),
                "title": mr.get("title"),
                "state": mapped_state,
                "created_at": mr.get("created_at"),
                "updated_at": mr.get("updated_at"),
                "closed_at": mr.get("closed_at"),
                "merged_at": merged_at,
                "user": {"login": mr.get("author", {}).get("username")} if mr.get("author") else None,
                "html_url": mr.get("web_url"),
                "additions": None,
                "deletions": None,
                "changed_files": None,
                "comments": None,
                "review_comments": None,
                "commits": None,
            }
            mr_list.append(mr_dict)

        next_page = response.headers.get("X-Next-Page")
        if next_page and next_page.strip():
            params["page"] = int(next_page)
        elif len(data) < params["per_page"]:
            break
        else:
            params["page"] += 1

    return mr_list


def runGitLabMrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    GitLab MR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "github_pr_analysis_available": False,
    }

    env_token = os.getenv("GITLAB_TOKEN") or os.getenv("GL_TOKEN")
    final_token = env_token or token

    info = extractGitLabRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a GitLab repository or remote URL. Skipping MR analysis.")
        return defaults

    domain, project_path, repo_name = info

    try:
        prs = fetchGitLabMergeRequests(domain, project_path, final_token)

        metrics = calculatePrMetrics(prs)
        metrics["github_pr_analysis_available"] = True

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"GitLab MR analysis failed: {e}. Falling back to default metrics.")
        return defaults


def enrichViaGitLabApi(
    domain: str,
    project_path: str,
    token: Optional[str],
) -> Dict[str, Any]:
    """
    Optional GitLab API enrichment.
    """
    import urllib.parse
    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded_path = urllib.parse.quote_plus(project_path)
    url = f"https://{domain}/api/v4/projects/{encoded_path}"

    response = callGitLabApi(url, headers)
    if not response:
        return {"available": False, "reason": "Request failed"}

    try:
        project = response.json()
        
        created_at_str = "N/A"
        if project.get("created_at"):
            try:
                created_at_str = project.get("created_at").split("T")[0]
            except Exception:
                pass
                
        pushed_at_str = "N/A"
        if project.get("last_activity_at"):
            try:
                pushed_at_str = project.get("last_activity_at").split("T")[0]
            except Exception:
                pass

        license_info = project.get("license")
        license_spdx = license_info.get("spdx_id") if license_info else None

        size_bytes = project.get("statistics", {}).get("repository_size", 0)
        size_kb = size_bytes // 1024 if size_bytes else 0

        return {
            "available":       True,
            "full_name":       project.get("path_with_namespace"),
            "owner":           project.get("namespace", {}).get("path"),
            "description":     project.get("description"),
            "is_private":      project.get("visibility") == "private",
            "is_fork":         project.get("forked_from_project") is not None,
            "stars":           project.get("star_count", 0),
            "forks":           project.get("forks_count", 0),
            "open_issues":     project.get("open_issues_count", 0),
            "topics":          project.get("topics", project.get("tag_list", [])),
            "default_branch":  project.get("default_branch"),
            "created_at":      created_at_str,
            "pushed_at":       pushed_at_str,
            "size_kb":         size_kb,
            "license":         license_spdx,
            "language":        None,
            "subscribers":     None,
            "visibility":      project.get("visibility"),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

def extractBitbucketRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str, str, bool]]:
    """
    Detect Bitbucket domain, workspace/project, and repo slug from inputTarget URL
    or local git configuration.
    Returns: (domain, workspace_or_project, repo_slug, is_cloud)
    """
    import urllib.parse

    for candidate in [inputTarget, targetDir]:
        if not candidate:
            continue

        # 1. Handle HTTP/HTTPS URLs (including ones with embedded tokens/passwords)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            try:
                parsed = urllib.parse.urlparse(candidate)
                hostname = parsed.hostname
                if hostname and "bitbucket" in hostname.lower():
                    is_cloud = hostname.lower() == "bitbucket.org"
                    path = parsed.path.strip("/")
                    if path.endswith(".git"):
                        path = path[:-4]

                    # Bitbucket Server/Data Center paths look like:
                    #   scm/PROJECT/repo.git
                    #   projects/PROJECT/repos/repo/browse
                    path = re.sub(r"^scm/", "", path)
                    path = re.sub(r"^projects/([^/]+)/repos/([^/]+).*$", r"\1/\2", path)

                    parts = path.split("/")
                    if len(parts) >= 2:
                        workspace = parts[0]
                        repo = parts[1]
                        return hostname, workspace, repo, is_cloud
            except Exception:
                pass

        # 2. Handle SSH and other Git URL formats
        #    Cloud:  git@bitbucket.org:workspace/repo.git
        #    Server: ssh://git@bitbucket.company.com:7999/PROJECT/repo.git
        else:
            if "bitbucket" in candidate.lower():
                try:
                    stripped = candidate
                    if stripped.startswith("ssh://"):
                        stripped = stripped[len("ssh://"):]

                    if "@" in stripped:
                        host_and_path = stripped.split("@", 1)[1]
                    else:
                        host_and_path = stripped

                    # host_and_path is one of:
                    #   bitbucket.org:workspace/repo.git
                    #   bitbucket.company.com:7999/PROJECT/repo.git
                    m = re.split(r'[:/]', host_and_path, 1)
                    if len(m) == 2:
                        domain = m[0]
                        rest = m[1]

                        # Strip a leading port number if present (server SSH URLs)
                        rest = re.sub(r'^\d+/', '', rest)

                        if rest.endswith(".git"):
                            rest = rest[:-4]

                        rest = re.sub(r"^scm/", "", rest)

                        parts = rest.split("/")
                        if len(parts) >= 2:
                            is_cloud = domain.lower() == "bitbucket.org"
                            workspace = parts[0]
                            repo = parts[1]
                            return domain, workspace, repo, is_cloud
                except Exception:
                    pass

    # 3. Check git config remote origin as a fallback
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            return extractBitbucketRepoInfo(targetDir, remote_url)

    return None

def _bitbucketAuthHeaders(token: Optional[str], is_cloud: bool) -> dict:
    """
    Builds auth headers for Bitbucket API calls.
    Cloud: Bearer token (Repository/Workspace access token) works for both
           read scopes; falls back to no auth for public repos.
    Server/Data Center: Bearer token (Personal Access Token).
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def callBitbucketApi(url: str, headers: dict, params: dict = None) -> Optional[Any]:
    """
    Executes a Bitbucket API request with rate limit handling and retries.
    """
    import requests
    retries = 0
    max_retries = 5
    backoff = 2
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (backoff ** retries)
                logger.warning(f"Bitbucket API rate limit hit. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"Bitbucket API server error ({response.status_code}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [401, 403]:
                logger.error(f"Bitbucket API auth failed ({response.status_code}): {response.text}")
                return None
            else:
                logger.error(f"Bitbucket API request failed with status code {response.status_code}: {response.text}")
                return None
        except requests.RequestException as e:
            sleep_time = backoff ** retries
            logger.warning(f"Network error during Bitbucket API call: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    return None


def enrichViaBitbucketApi(
    domain: str,
    workspace: str,
    repo: str,
    token: Optional[str],
    is_cloud: bool,
) -> Dict[str, Any]:
    """
    Optional Bitbucket API enrichment. Branches between Bitbucket Cloud (API v2.0)
    and Bitbucket Server/Data Center (REST API 1.0), which have different
    endpoints, auth, and response shapes.
    """
    import urllib.parse
    headers = _bitbucketAuthHeaders(token, is_cloud)

    if is_cloud:
        encoded_workspace = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://api.bitbucket.org/2.0/repositories/{encoded_workspace}/{encoded_repo}"

        response = callBitbucketApi(url, headers)
        if not response:
            return {"available": False, "reason": "Request failed"}

        try:
            data = response.json()

            created_at_str = "N/A"
            if data.get("created_on"):
                try:
                    created_at_str = data.get("created_on").split("T")[0]
                except Exception:
                    pass

            pushed_at_str = "N/A"
            if data.get("updated_on"):
                try:
                    pushed_at_str = data.get("updated_on").split("T")[0]
                except Exception:
                    pass

            size_bytes = data.get("size", 0)
            size_kb = size_bytes // 1024 if size_bytes else 0

            return {
                "available":       True,
                "full_name":       data.get("full_name"),
                "owner":           workspace,
                "description":     data.get("description"),
                "is_private":      data.get("is_private", False),
                "is_fork":         data.get("parent") is not None,
                "stars":           None,   # Bitbucket Cloud has no star count
                "forks":           None,   # requires separate /forks endpoint
                "open_issues":     None,   # requires separate /issues endpoint
                "topics":          [],     # Bitbucket has no topics/tags concept
                "default_branch":  (data.get("mainbranch") or {}).get("name"),
                "created_at":      created_at_str,
                "pushed_at":       pushed_at_str,
                "size_kb":         size_kb,
                "license":         None,   # not exposed by the repo endpoint
                "language":        data.get("language"),
                "subscribers":     None,
                "visibility":      "private" if data.get("is_private") else "public",
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    else:
        # Bitbucket Server / Data Center — REST API 1.0
        encoded_project = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://{domain}/rest/api/1.0/projects/{encoded_project}/repos/{encoded_repo}"

        response = callBitbucketApi(url, headers)
        if not response:
            return {"available": False, "reason": "Request failed"}

        try:
            data = response.json()

            default_branch = None
            try:
                branch_url = f"{url}/default-branch"
                branch_resp = callBitbucketApi(branch_url, headers)
                if branch_resp:
                    default_branch = branch_resp.json().get("displayId")
            except Exception:
                pass

            is_public = data.get("public", False)

            return {
                "available":       True,
                "full_name":       f"{workspace}/{data.get('slug', repo)}",
                "owner":           workspace,
                "description":     data.get("description"),
                "is_private":      not is_public,
                "is_fork":         data.get("origin") is not None,
                "stars":           None,
                "forks":           None,
                "open_issues":     None,
                "topics":          [],
                "default_branch":  default_branch,
                "created_at":      "N/A",   # not exposed by Server API
                "pushed_at":       "N/A",
                "size_kb":         None,    # requires separate size endpoint
                "license":         None,
                "language":        None,
                "subscribers":     None,
                "visibility":      "public" if is_public else "private",
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}


def fetchBitbucketPullRequests(
    domain: str,
    workspace: str,
    repo: str,
    token: Optional[str],
    is_cloud: bool,
) -> List[Dict[str, Any]]:
    """
    Fetch all Pull Requests for the repository using the Bitbucket API, paginating.
    Cloud and Server differ significantly in pagination and state filtering,
    so this branches on is_cloud.
    """
    import urllib.parse
    pr_list = []
    headers = _bitbucketAuthHeaders(token, is_cloud)
    logger.info("Fetching Bitbucket Pull Requests...")

    if is_cloud:
        encoded_workspace = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        base_url = f"https://api.bitbucket.org/2.0/repositories/{encoded_workspace}/{encoded_repo}/pullrequests"

        # Bitbucket Cloud doesn't support state=ALL in one call — must query
        # each state separately and merge the results.
        for state in ("OPEN", "MERGED", "DECLINED", "SUPERSEDED"):
            url = base_url
            params = {"state": state, "pagelen": 50}

            while url:
                response = callBitbucketApi(url, headers, params)
                if not response:
                    break

                data = response.json()
                values = data.get("values", [])
                if not values:
                    break

                for pr in values:
                    mapped_state = "closed"
                    if pr.get("state") == "OPEN":
                        mapped_state = "open"

                    merged_at = pr.get("updated_on") if pr.get("state") == "MERGED" else None

                    pr_dict = {
                        "number": pr.get("id"),
                        "title": pr.get("title"),
                        "state": mapped_state,
                        "created_at": pr.get("created_on"),
                        "updated_at": pr.get("updated_on"),
                        "closed_at": pr.get("updated_on") if pr.get("state") in ("MERGED", "DECLINED") else None,
                        "merged_at": merged_at,
                        "user": {"login": (pr.get("author") or {}).get("nickname")} if pr.get("author") else None,
                        "html_url": (pr.get("links", {}).get("html") or {}).get("href"),
                        "additions": None,
                        "deletions": None,
                        "changed_files": None,
                        "comments": pr.get("comment_count"),
                        "review_comments": None,
                        "commits": None,
                    }
                    pr_list.append(pr_dict)

                # Cloud pagination gives a full "next" URL directly
                url = data.get("next")
                params = None  # already encoded in the next URL

    else:
        # Bitbucket Server / Data Center — REST API 1.0, start/limit pagination
        encoded_project = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://{domain}/rest/api/1.0/projects/{encoded_project}/repos/{encoded_repo}/pull-requests"

        start = 0
        limit = 100
        while True:
            params = {"state": "ALL", "start": start, "limit": limit}
            response = callBitbucketApi(url, headers, params)
            if not response:
                break

            data = response.json()
            values = data.get("values", [])
            if not values:
                break

            for pr in values:
                state = pr.get("state")  # OPEN, MERGED, DECLINED
                mapped_state = "open" if state == "OPEN" else "closed"

                created_ms = pr.get("createdDate")
                updated_ms = pr.get("updatedDate")
                closed_ms = pr.get("closedDate")

                def _ms_to_iso(ms):
                    if not ms:
                        return None
                    try:
                        return datetime.datetime.utcfromtimestamp(ms / 1000).isoformat() + "Z"
                    except Exception:
                        return None

                pr_dict = {
                    "number": pr.get("id"),
                    "title": pr.get("title"),
                    "state": mapped_state,
                    "created_at": _ms_to_iso(created_ms),
                    "updated_at": _ms_to_iso(updated_ms),
                    "closed_at": _ms_to_iso(closed_ms) if state in ("MERGED", "DECLINED") else None,
                    "merged_at": _ms_to_iso(closed_ms) if state == "MERGED" else None,
                    "user": {"login": (pr.get("author", {}).get("user") or {}).get("name")} if pr.get("author") else None,
                    "html_url": next(
                        (l.get("href") for l in pr.get("links", {}).get("self", [])), None
                    ) if pr.get("links") else None,
                    "additions": None,
                    "deletions": None,
                    "changed_files": None,
                    "comments": None,
                    "review_comments": None,
                    "commits": None,
                }
                pr_list.append(pr_dict)

            if data.get("isLastPage", True):
                break
            start = data.get("nextPageStart", start + limit)

    return pr_list


def runBitbucketPrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    Bitbucket PR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "github_pr_analysis_available": False,
    }

    env_token = os.getenv("BITBUCKET_TOKEN") or os.getenv("BB_TOKEN")
    final_token = env_token or token

    info = extractBitbucketRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a Bitbucket repository or remote URL. Skipping PR analysis.")
        return defaults

    domain, workspace, repo_name, is_cloud = info

    if not final_token:
        logger.warning(
            "Bitbucket token missing. Please set BITBUCKET_TOKEN or BB_TOKEN env variables "
            "(or pass --bitbucket-token) to enable Bitbucket PR analytics."
        )
        return defaults

    try:
        prs = fetchBitbucketPullRequests(domain, workspace, repo_name, final_token, is_cloud)

        metrics = calculatePrMetrics(prs)
        metrics["github_pr_analysis_available"] = True

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"Bitbucket PR analysis failed: {e}. Falling back to default metrics.")
        return defaults

def extractGitHubRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str]]:
    """
    Detect GitHub repository owner and name from inputTarget URL or local git configuration.
    """
    for candidate in [inputTarget, targetDir]:
        if candidate:
            m = re.search(r"github\.com[/:]([^/]+)/([^/]+)", candidate)
            if m:
                owner = m.group(1)
                repo = m.group(2)
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return owner, repo

    # If not found directly, check git config remote origin
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            m = re.search(r"github\.com[/:]([^/]+)/([^/]+)", remote_url)
            if m:
                owner = m.group(1)
                repo = m.group(2)
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return owner, repo
    return None


def getCoreRateLimit(g: Github):
    """
    Safely get the core rate limit object from Github instance,
    supporting different PyGithub versions.
    """
    try:
        rate_limit = g.get_rate_limit()
        if hasattr(rate_limit, "resources") and hasattr(rate_limit.resources, "core"):
            return rate_limit.resources.core
        elif hasattr(rate_limit, "core"):
            return rate_limit.core
        elif hasattr(rate_limit, "rate"):
            return rate_limit.rate
    except Exception as e:
        logger.warning(f"Failed to get rate limit object: {e}")
    return None


def handleRateLimit(g: Github):
    """
    Checks the current GitHub rate limit and sleeps if it is close to exhausted.
    """
    core = getCoreRateLimit(g)
    if core is None:
        return
    try:
        if core.remaining < 10:
            reset_val = core.reset
            if hasattr(reset_val, "timestamp"):
                reset_time = reset_val.timestamp()
            else:
                reset_time = float(reset_val)
            now = time.time()
            sleep_duration = max(0.0, reset_time - now) + 5
            logger.warning(
                f"GitHub API Rate limit low ({core.remaining}/{core.limit}). "
                f"Sleeping for {sleep_duration:.1f} seconds until reset..."
            )
            time.sleep(sleep_duration)
    except Exception as e:
        logger.warning(f"Failed to check rate limit: {e}")


def callWithRetry(g: Github, func, *args, max_retries=5, **kwargs):
    """
    Executes a PyGithub function with rate limit checks and retries.
    """
    retries = 0
    backoff = 2
    while retries < max_retries:
        try:
            handleRateLimit(g)
            return func(*args, **kwargs)
        except RateLimitExceededException:
            logger.warning("Rate limit exceeded during GitHub API call. Checking/sleeping for reset...")
            handleRateLimit(g)
            retries += 1
        except GithubException as e:
            if getattr(e, "status", None) == 403 and "abuse" in str(e).lower():
                sleep_time = backoff ** retries
                logger.warning(f"GitHub abuse detection triggered. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif getattr(e, "status", None) in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"GitHub API server error ({e.status}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            else:
                raise e
        except Exception as e:
            sleep_time = backoff ** retries
            logger.warning(f"Unexpected GitHub API error: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    raise Exception(f"Failed after {max_retries} retries")


def fetchPullRequests(g: Github, repo) -> List[Dict[str, Any]]:
    """
    Fetch all PRs for the repository using PyGithub, paginating and handling rate limits.
    """
    pr_list = []
    logger.info("Fetching GitHub Pull Requests...")

    pulls = repo.get_pulls(state="all")

    core = getCoreRateLimit(g)
    if core is not None:
        remaining = core.remaining
    else:
        remaining = 5000  # Default fallback

    logger.info(f"Initial GitHub API rate limit: {remaining} remaining")

    count = 0
    for pr in pulls:
        count += 1
        if count % 100 == 0:
            logger.info(f"Fetched {count} PR metadata entries...")

        pr_dict = {}
        try:
            pr_dict = dict(pr._raw_data) if hasattr(pr, "_raw_data") and pr._raw_data else {}
        except Exception:
            pass

        pr_dict.update({
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "user": {"login": pr.user.login} if pr.user else None,
            "html_url": pr.html_url,
        })

        # Detailed attributes: additions, deletions, changed_files, commits, comments, review_comments
        try:
            if count % 100 == 0:
                try:
                    rate_limit = g.get_rate_limit().core
                    remaining = rate_limit.remaining
                except Exception:
                    pass

            # If remaining rate limit is low (e.g. < 150), skip detailed fetch
            if remaining < 150:
                pr_dict.update({
                    "additions": None,
                    "deletions": None,
                    "changed_files": None,
                    "comments": getattr(pr, "comments", None),
                    "review_comments": getattr(pr, "review_comments", None),
                    "commits": None,
                })
            else:
                def _fetch_details():
                    # Accessing additions triggers the detailed GET request
                    return (pr.additions, pr.deletions, pr.changed_files, pr.commits, pr.comments, pr.review_comments)

                additions, deletions, changed_files, commits, comments, review_comments = callWithRetry(
                    g, _fetch_details)
                pr_dict.update({
                    "additions": additions,
                    "deletions": deletions,
                    "changed_files": changed_files,
                    "comments": comments,
                    "review_comments": review_comments,
                    "commits": commits,
                })
        except Exception:
            # Fallback to None/safe defaults for detailed fields on error
            pr_dict.update({
                "additions": None,
                "deletions": None,
                "changed_files": None,
                "comments": getattr(pr, "comments", None),
                "review_comments": getattr(pr, "review_comments", None),
                "commits": None,
            })

        pr_list.append(pr_dict)

    return pr_list


def calculatePrMetrics(prs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate PR counts based on the retrieved PR list.
    """
    total_pr_count = len(prs)
    open_pr_count = 0
    closed_pr_count = 0
    merged_pr_count = 0

    for pr in prs:
        state = pr.get("state")
        merged_at = pr.get("merged_at")

        if state == "open":
            open_pr_count += 1
        elif state == "closed":
            closed_pr_count += 1
            if merged_at is not None:
                merged_pr_count += 1

    return {
        "total_pr_count": total_pr_count,
        "open_pr_count": open_pr_count,
        "closed_pr_count": closed_pr_count,
        "merged_pr_count": merged_pr_count,
        "github_pr_analysis_available": True,
    }


def sanitizeFilename(name: str) -> str:
    """Sanitize the repository name to make it OS-safe."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def dumpPrJsonFiles(outputDir: str, sanitizedRepoName: str, prs: List[Dict[str, Any]]):
    """
    Dump all, open, closed, and merged PR lists into JSON files in outputDir.
    """
    os.makedirs(outputDir, exist_ok=True)

    open_prs = [pr for pr in prs if pr.get("state") == "open"]
    closed_prs = [pr for pr in prs if pr.get("state") == "closed"]
    merged_prs = [pr for pr in prs if pr.get("state") == "closed" and pr.get("merged_at") is not None]

    files_to_write = {
        f"all_prs_{sanitizedRepoName}.json": prs,
        f"open_prs_{sanitizedRepoName}.json": open_prs,
        f"closed_prs_{sanitizedRepoName}.json": closed_prs,
        f"merged_prs_{sanitizedRepoName}.json": merged_prs,
    }

    for filename, data in files_to_write.items():
        filepath = os.path.join(outputDir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved PR JSON dump: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save PR JSON dump {filepath}: {e}")


def runGitHubPrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    GitHub PR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "github_pr_analysis_available": False,
    }

    env_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    final_token = env_token or token

    info = extractGitHubRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a GitHub repository or remote URL. Skipping PR analysis.")
        return defaults

    owner, repo_name = info

    if not final_token:
        logger.warning(
            "GitHub token missing. Please set GITHUB_TOKEN or GH_TOKEN env variables "
            "(or pass --github-token) to enable GitHub PR analytics."
        )
        return defaults

    if not HAS_PYGITHUB:
        logger.warning("PyGithub is not installed. Skipping GitHub PR analytics.")
        return defaults

    try:
        try:
            from github import Auth
            auth = Auth.Token(final_token)
            g = Github(auth=auth)
        except (ImportError, AttributeError):
            g = Github(final_token)
        repo = g.get_repo(f"{owner}/{repo_name}")

        prs = fetchPullRequests(g, repo)

        metrics = calculatePrMetrics(prs)

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"GitHub PR analysis failed: {e}. Falling back to default metrics.")
        return defaults


def enrichViaGithubApi(
    repoUrl: str,
    token: Optional[str],
) -> Dict[str, Any]:
    """
    Optional GitHub API enrichment (requires PyGithub + valid token for private repos).
    Fetches: open/closed status, stars, forks, topics, description, visibility,
    subscriber count, default branch, and whether repo was ever public.
    """
    if not HAS_PYGITHUB:
        return {"available": False, "reason": "PyGithub not installed"}
    if not token:
        return {"available": False, "reason": "No GitHub token provided"}
    m = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repoUrl)
    if not m:
        return {"available": False, "reason": "Cannot parse GitHub URL"}

    try:
        g = Github(token)
        repo = g.get_repo(f"{m.group(1)}/{m.group(2)}")


        return {
            "available":       True,
            "full_name":       repo.full_name,
            "owner":           repo.owner.login,
            "description":     repo.description,
            "is_private":      repo.private,
            "is_fork":         repo.fork,
            "stars":           repo.stargazers_count,
            "forks":           repo.forks_count,
            "open_issues":     repo.open_issues_count,
            "topics":          repo.get_topics(),
            "default_branch":  repo.default_branch,
            "created_at":      str(repo.created_at.date()),
            "pushed_at":       str(repo.pushed_at.date()),
            "size_kb":         repo.size,
            "license":         (repo.license.spdx_id if repo.license else None),
            "language":        repo.language,
            "subscribers":     repo.subscribers_count,  # Watchers
            "visibility":      "private" if repo.private else "public",
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def saveReport(
    reportData: Dict[str, Any],
    repoPath: str,
    inputTarget: str,
    outputRootDir: str,
    isUrl: bool
) -> str:
    """
    Save analysis report to JSON and generate two separate summary CSVs.
    """
    absRepo = os.path.abspath(repoPath)
    repoName = os.path.basename(absRepo)

    if isUrl:
        safeName = inputTarget.rstrip("/").split("/")[-1].replace(".git", "")
        outDir = os.path.join(outputRootDir, safeName)
    else:
        outDir = os.path.join(outputRootDir, repoName)

    os.makedirs(outDir, exist_ok=True)
    outFile = os.path.join(outDir, f"{repoName}_report.json")

    gt = reportData.get("ground_truth", {})
    tm = reportData.get("tool_metrics", {})
    he = reportData.get("heuristics", {})
    gh = reportData.get("github_api", {})

    primaryLangs = gt.get("languages", {}).get("core_list", [])
    primaryLangs = list(dict.fromkeys([LANG_MERGE_MAP.get(lang, lang) for lang in primaryLangs]))

    frontendLangs = sorted(list(set(primaryLangs) & FRONTEND_LANGUAGES))
    backendLangs = sorted(list(set(primaryLangs) & BACKEND_LANGUAGES))

    allFwFound = set()
    for fw_list in he.get("frameworks", {}).values():
        for fw in fw_list:
            # Normalize to formal name if known, otherwise keep as is
            normFw = FORMAL_FRAMEWORK_NAMES.get(fw.lower(), fw)
            allFwFound.add(normFw)

    # Case-insensitive deduplication for the final sets
    def _dedupe_fws(fws, category_set):
        seen_lower = set()
        unique_fws = []
        for f in sorted(list(fws)):
            if f.lower() in category_set and f.lower() not in seen_lower:
                unique_fws.append(f)
                seen_lower.add(f.lower())
        return unique_fws

    frontendFws = _dedupe_fws(allFwFound, FRONTEND_FRAMEWORKS)
    backendFws = _dedupe_fws(allFwFound, BACKEND_FRAMEWORKS)

    lexTokCount = tm.get("tokens", {}).get("lexical", 0)
    infra = he.get("infrastructure", {})
    doc = he.get("docs", {})

    freq = 0
    if gt.get("git", {}).get("active_span_months", 0) > 0:
        freq = round(gt.get("git", {}).get("commit_count", 0) / gt.get("git", {}).get("active_span_months"), 1)

    sec_count = tm.get("secrets", {}).get("findings_count", 0)
    sec_status = "Clean" if tm.get("secrets", {}).get("clean", True) else f"Review Required ({sec_count})"

    # Note: Recalculated lang percent is not used directly since byte percent is output below

    byte_breakdown = reportData.get("heuristics", {}).get("byte_breakdown", {})
    consolidatedLangs = defaultdict(int)
    consolidatedFws = defaultdict(int)

    for label, byte_count in byte_breakdown.items():
        if label in NON_CORE_FORMATS:
            continue

        if "(" in label:
            parts = label.split("(")
            fwName = parts[0].strip()
            langName = parts[1].replace(")", "").strip()
            identityLang = LANG_MERGE_MAP.get(langName, langName)
            consolidatedLangs[identityLang] += byte_count
            consolidatedFws[fwName] += byte_count
        else:
            identityLang = LANG_MERGE_MAP.get(label, label)
            consolidatedLangs[identityLang] += byte_count

    totalLangBytes = sum(consolidatedLangs.values())
    langBytePctStr = "N/A"
    if totalLangBytes > 0:
        lang_pcts = []
        for name, size in consolidatedLangs.items():
            pct = (size / totalLangBytes) * 100
            if pct >= 5.0:
                lang_pcts.append((name, pct))
        lang_pcts.sort(key=lambda x: x[1], reverse=True)
        langBytePctStr = " | ".join([f"{name} ({pct:.1f}%)" for name, pct in lang_pcts]) or "None > 5%"

    # Process Framework %
    totalFwBytes = sum(consolidatedFws.values())
    fwBytePctStr = "N/A"
    if totalFwBytes > 0:
        fw_pcts = []
        for name, size in consolidatedFws.items():
            pct = (size / totalFwBytes) * 100
            if pct >= 5.0:
                fw_pcts.append((name, pct))
        fw_pcts.sort(key=lambda x: x[1], reverse=True)
        fwBytePctStr = " | ".join([f"{name} ({pct:.1f}%)" for name, pct in fw_pcts]) or "None > 5%"
    else:
        fwBytePctStr = "None Detected"

    prs_data = reportData.get("github_prs", {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "github_pr_analysis_available": False,
    })

    all_headers = [
        "repo_name", "is_git", "license_type",
        "first_commit_date", "last_commit_date",
        "loc_code", "loc_comment", "loc_blank", "loc_files",
        "lang_count", "languages", "languages_frontend", "languages_backend",
        "commits", "contributors", "all_contributors_count",
        "all_contributors", "branch_count", "meaningful_commit_count",
        "development_span_months", "commits_per_month", "git_history_intact",
        "tokens_llm", "lexical_token", "duplication_weighted_percent",
        "code_complexity", "ai_detection_percent", "frameworks",
        "framework_frontend", "framework_backend",
        "databases_used", "third_party_apis", "setup_guidelines",
        "security_findings", "documentation_quality",
        "repo_rating_score", "repo_rating_label",
        "languages_percentage_bytes(>5%)", "frameworks_percentage_bytes(>5%)",
        "total_time_seconds",
        "total_pr_count", "open_pr_count", "closed_pr_count",
        "merged_pr_count", "github_pr_analysis_available"
    ]

    all_row = {
        "repo_name":          reportData.get("repo"),
        "is_git":             reportData.get("is_git"),
        "license_type":       tm.get("compliance", {}).get("license_type", "N/A"),
        "first_commit_date":  gt.get("git", {}).get("first_commit", "N/A"),
        "last_commit_date":   gt.get("git", {}).get("last_update", "N/A"),
        "loc_code":           gt.get("loc", {}).get("breakdown", {}).get("code", 0),
        "loc_comment":        gt.get("loc", {}).get("breakdown", {}).get("comment", 0),
        "loc_blank":          gt.get("loc", {}).get("breakdown", {}).get("blank", 0),
        "loc_files":          gt.get("loc", {}).get("breakdown", {}).get("nFiles", 0),
        "lang_count":         len(primaryLangs),
        "languages":          " | ".join(primaryLangs),
        "languages_frontend": " | ".join(frontendLangs),
        "languages_backend":  " | ".join(backendLangs),
        "commits":            gt.get("git", {}).get("commit_count", 0),
        "contributors":       gt.get("git", {}).get("unique_contributors", 0),
        "all_contributors_count": gt.get("git", {}).get("all_contributors_count", 0),
        "all_contributors":   ", ".join([c.get("name", "") for c in gt.get("git", {}).get("all_contributors", [])]),
        "branch_count":       gt.get("git", {}).get("branch_count", 0),
        "meaningful_commit_count": gt.get("git", {}).get("meaningful_commit_count", 0),
        "development_span_months": gt.get("git", {}).get("active_span_months", 0),
        "commits_per_month":  f"{freq} commits/mo",
        "git_history_intact": gt.get("git", {}).get("history_integrity", {}).get("appears_intact", False),
        "tokens_llm":         tm.get("tokens", {}).get("llm", 0),
        "lexical_token":      lexTokCount,
        "duplication_weighted_percent":   f"{tm.get('duplication', {}).get('token_weighted', 0)*100:.1f}%",
        "code_complexity":    he.get("complexity", "Low"),
        "ai_detection_percent": f"{he.get('ai_detection', {}).get('repo_score', 0)*100:.1f}%",
        "frameworks":         " | ".join([f"{k}: {','.join(v)}" for k, v in he.get("frameworks", {}).items()]),
        "framework_frontend": " | ".join(frontendFws),
        "framework_backend":  " | ".join(backendFws),
        "databases_used":     " | ".join(infra.get("databases", [])),
        "third_party_apis":   " | ".join(infra.get("apis", [])),
        "setup_guidelines":   "Present" if doc.get("has_setup") else "Not found in README",
        "security_findings":  sec_status,
        "documentation_quality": doc.get("doc_quality", "Low"),
        "repo_rating_score":  he.get("repo_rating", {}).get("rating", 0),
        "repo_rating_label":  he.get("repo_rating", {}).get("label", "N/A"),
        "languages_percentage_bytes(>5%)": langBytePctStr,
        "frameworks_percentage_bytes(>5%)": fwBytePctStr,
        "total_time_seconds": reportData.get("performance", {}).get("total_seconds", 0),
        "total_pr_count":     prs_data.get("total_pr_count", 0),
        "open_pr_count":      prs_data.get("open_pr_count", 0),
        "closed_pr_count":     prs_data.get("closed_pr_count", 0),
        "merged_pr_count":     prs_data.get("merged_pr_count", 0),
        "github_pr_analysis_available": prs_data.get("github_pr_analysis_available", False),
    }

    meta_headers = [
        "repo_name", "lexical_token", "framework_frontend", "framework_backend",
        "languages_frontend", "languages_backend",
        "language_framework_details", "domain_industry",
        "commercial_usage_summary", "security_scrubbing_confirmation",
        "full_git_history", "repo_rating_score", "first_commit_date", "last_commit_date",
        "languages_percentage_bytes(>5%)", "frameworks_percentage_bytes(>5%)",
        "development_span_months", "commits_per_month", "unique_contributors",
        "all_contributors_count", "all_contributors",
        "total_pr_count", "open_pr_count", "closed_pr_count", "merged_pr_count", "github_pr_analysis_available"
    ]

    license_type_str = tm.get("compliance", {}).get("license_type", "Unknown") or "Unknown"
    is_permissive = any(word in license_type_str.lower() for word in ["mit", "apache", "bsd", "isc"])
    commercial_summary = "Permissive" if is_permissive else "Restrictive"

    meta_row = {
        "repo_name":                  reportData.get("repo"),
        "lexical_token":              lexTokCount,
        "framework_frontend":         "|".join(frontendFws),
        "framework_backend":          "|".join(backendFws),
        "languages_frontend":         "|".join(frontendLangs),
        "languages_backend":          "|".join(backendLangs),
        "language_framework_details": "|".join([f"{k}:{','.join(v)}" for k, v in he.get("frameworks", {}).items()]),
        "domain_industry":            "Unknown",
        "commercial_usage_summary":   commercial_summary,
        "security_scrubbing_confirmation": sec_status,
        "full_git_history":           all_row["git_history_intact"],
        "repo_rating_score":          all_row["repo_rating_score"],
        "first_commit_date":          all_row["first_commit_date"],
        "last_commit_date":           all_row["last_commit_date"],
        "languages_percentage_bytes(>5%)": all_row["languages_percentage_bytes(>5%)"],
        "frameworks_percentage_bytes(>5%)": all_row["frameworks_percentage_bytes(>5%)"],
        "development_span_months":    all_row["development_span_months"],
        "commits_per_month":          all_row["commits_per_month"],
        "unique_contributors":        all_row["contributors"],
        "all_contributors_count":     all_row["all_contributors_count"],
        "all_contributors":           all_row["all_contributors"],
        "total_pr_count":             prs_data.get("total_pr_count", 0),
        "open_pr_count":              prs_data.get("open_pr_count", 0),
        "closed_pr_count":             prs_data.get("closed_pr_count", 0),
        "merged_pr_count":             prs_data.get("merged_pr_count", 0),
        "github_pr_analysis_available": prs_data.get("github_pr_analysis_available", False),
    }

    reportData["full_report_snapshot"] = {
        **all_row,  # Include everything currently in all_row
        "deployment_env":     "|".join(infra.get("deployment", [])),
        "environment_variables": doc.get("env_vars", "N/A"),
        "stars":              gh.get("stars", "N/A"),
        "forks":              gh.get("forks", "N/A"),
        "watchers":           gh.get("subscribers", "N/A"),
        "open_issues":        gh.get("open_issues", "N/A"),
        "creation_date":      gh.get("created_at", "N/A"),
        "loc_verified":       gt.get("loc", {}).get("value", 0),
        "tokenizer_method":   tm.get("tokens", {}).get("tokenizer", "N/A"),
    }

    with open(outFile, "w", encoding="utf-8") as f:
        json.dump(reportData, f, indent=2)

    def appendToCsv(filename, headers, row_data):
        path = os.path.join(outputRootDir, filename)
        exists = os.path.isfile(path)
        with CSV_LOCK:
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not exists:
                    writer.writeheader()
                writer.writerow(row_data)

    pass

    appendToCsv("summary_all.csv", all_headers, all_row)
    appendToCsv("summary_metadata.csv", meta_headers, meta_row)

    legacy_headers = [
        "Repository Name",
        "Is Git Repository",
        "License Type",
        "Git History Intact",
        "First Commit Date",
        "Last Commit Date",
        "Development Span (Months)",
        "Lines of Code (Code)",
        "Lines of Code (Comments)",
        "Lines of Code (Blank)",
        "Total Files",
        "Total Commits",
        "Meaningful Commit Count",
        "Commits per Month",
        "total_pr_count",
        "open_pr_count",
        "closed_pr_count",
        "merged_pr_count",
        "github_pr_analysis_available",
        "Number of Contributors with commits",
        "Total Contributors (All-Time)",
        "Contributor List",
        "Branch Count",
        "LLM Tokens",
        "Lexical Tokens",
        "Code Duplication (Weighted %)",
        "Code Complexity",
        "AI Detection Percentage",
        "Language Count",
        "Languages Used",
        "Frontend Languages",
        "Backend Languages",
        "Frameworks Used",
        "Frontend Frameworks",
        "Backend Frameworks",
        "Languages Percentage (Bytes > 5%)",
        "Frameworks Percentage (Bytes > 5%)",
        "Databases Used",
        "Third-Party APIs",
        "Setup Guidelines",
        "Security Findings",
        "Documentation Quality",
        "Repository Rating Score"
    ]

    legacy_row = {
        "Repository Name": all_row.get("repo_name", ""),
        "Is Git Repository": all_row.get("is_git", ""),
        "License Type": all_row.get("license_type", ""),
        "Git History Intact": all_row.get("git_history_intact", ""),
        "First Commit Date": all_row.get("first_commit_date", ""),
        "Last Commit Date": all_row.get("last_commit_date", ""),
        "Development Span (Months)": all_row.get("development_span_months", ""),
        "Lines of Code (Code)": all_row.get("loc_code", ""),
        "Lines of Code (Comments)": all_row.get("loc_comment", ""),
        "Lines of Code (Blank)": all_row.get("loc_blank", ""),
        "Total Files": all_row.get("loc_files", ""),
        "Total Commits": all_row.get("commits", ""),
        "Meaningful Commit Count": all_row.get("meaningful_commit_count", ""),
        "Commits per Month": all_row.get("commits_per_month", ""),
        "total_pr_count": all_row.get("total_pr_count", ""),
        "open_pr_count": all_row.get("open_pr_count", ""),
        "closed_pr_count": all_row.get("closed_pr_count", ""),
        "merged_pr_count": all_row.get("merged_pr_count", ""),
        "github_pr_analysis_available": all_row.get("github_pr_analysis_available", ""),
        "Number of Contributors with commits": all_row.get("contributors", ""),
        "Total Contributors (All-Time)": all_row.get("all_contributors_count", ""),
        "Contributor List": all_row.get("all_contributors", ""),
        "Branch Count": all_row.get("branch_count", ""),
        "LLM Tokens": all_row.get("tokens_llm", ""),
        "Lexical Tokens": all_row.get("lexical_token", ""),
        "Code Duplication (Weighted %)": all_row.get("duplication_weighted_percent", ""),
        "Code Complexity": all_row.get("code_complexity", ""),
        "AI Detection Percentage": all_row.get("ai_detection_percent", ""),
        "Language Count": all_row.get("lang_count", ""),
        "Languages Used": all_row.get("languages", ""),
        "Frontend Languages": all_row.get("languages_frontend", ""),
        "Backend Languages": all_row.get("languages_backend", ""),
        "Frameworks Used": all_row.get("frameworks", ""),
        "Frontend Frameworks": all_row.get("framework_frontend", ""),
        "Backend Frameworks": all_row.get("framework_backend", ""),
        "Languages Percentage (Bytes > 5%)": all_row.get("languages_percentage_bytes(>5%)", ""),
        "Frameworks Percentage (Bytes > 5%)": all_row.get("frameworks_percentage_bytes(>5%)", ""),
        "Databases Used": all_row.get("databases_used", ""),
        "Third-Party APIs": all_row.get("third_party_apis", ""),
        "Setup Guidelines": all_row.get("setup_guidelines", ""),
        "Security Findings": all_row.get("security_findings", ""),
        "Documentation Quality": all_row.get("documentation_quality", ""),
        "Repository Rating Score": all_row.get("repo_rating_score", "")
    }

    appendToCsv("legacy.csv", legacy_headers, legacy_row)

    return outFile


def runInteractiveMode() -> Optional[argparse.Namespace]:
    """
    Guided terminal UI for configuring analysis parameters.
    Loops until valid configuration or exit.
    """
    while True:
        print("\n" + "=" * 34)
        print("   REPOSITORY ANALYSIS TOOL")
        print("=" * 34)
        print("1. Analyze Local Repository")
        print("2. Analyze Git Repository")
        print("3. Exit")

        choice = input("\nSelect an option: ").strip()

        if choice == "3":
            return None

        elif choice == "1":
            inputTarget = input("\nEnter local repository path: ").strip().strip('"')
            if not inputTarget or not os.path.isdir(inputTarget):
                print("[!] Error: Invalid or non-existent directory path.")
                continue

        elif choice == "2":
            inputTarget = input("\nEnter Git repository URL: ").strip().strip('"')
            if not (
                inputTarget.startswith("http://")
                or inputTarget.startswith("https://")
                or inputTarget.startswith("git@")
            ):
                print("[!] Error: Invalid Git URL.")
                continue

        else:
            print("[!] Invalid choice. Please select 1, 2, or 3.")
            continue

        print("\nSelect Analysis Mode:")
        print("1. Stage 1 (Pre-check)")
        print("2. Stage 2 (Deep Analysis)")
        print("3. Full Pipeline (Recommended)")
        mChoice = input("\nEnter choice: ").strip()

        mode = "full"
        if mChoice == "1":
            mode = "stage1"
        elif mChoice == "2":
            mode = "stage2"
        elif mChoice == "3":
            mode = "full"
        else:
            print("Invalid choice, defaulting to Full Pipeline.")

        print("\nConfigure Options:")
        mf = input("Max files to process (Press Enter for ALL files): ").strip()
        maxFiles = int(mf) if mf.isdigit() else None

        od = input("Output directory (default: ./outputs): ").strip()
        outDir = od if od else "./outputs"

        proceed = input("\nProceed? (y/n, or press Enter for yes): ").strip().lower()
        if proceed not in ("", "y", "yes"):
            continue

        print("\n" + "=" * 34)
        print("Execution Summary")
        print("=" * 34)
        print(f"Input Type   : {'Local' if choice == '1' else 'Git'}")
        print(f"Target       : {inputTarget}")
        print(f"Mode         : {mode.capitalize()}")
        print(f"Output Dir   : {outDir}")
        print(f"Max Files    : {maxFiles}")
        print("=" * 34)
        print("Starting analysis...\n")

        return argparse.Namespace(
            input=inputTarget,
            output_dir=outDir,
            clone_dir=None,
            mode=mode,
            max_files=maxFiles
        )


def runAnalysis(args: argparse.Namespace) -> bool:
    """
    Execute complete analysis pipeline (Stages 0–5).
    """
    inputTarget = args.input
    githubToken = getattr(args, "github_token", None)
    if not githubToken:
        githubToken = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    gitlabToken = getattr(args, "gitlab_token", None)
    if not gitlabToken:
        gitlabToken = os.getenv("GITLAB_TOKEN") or os.getenv("GL_TOKEN")
    bitbucketToken = getattr(args, "bitbucket_token", None)
    if not bitbucketToken:
        bitbucketToken = os.getenv("BITBUCKET_TOKEN") or os.getenv("BB_TOKEN")
    
    startTime = time.time()

    isUrl = (
        inputTarget.startswith("http://")
        or inputTarget.startswith("https://")
        or inputTarget.startswith("git@")
    )
    targetDir = inputTarget

    try:
        if isUrl:
            cloneBase = args.clone_dir if args.clone_dir else os.path.join(os.getcwd(), "cloned_repos")
            os.makedirs(cloneBase, exist_ok=True)

            repoName = os.path.basename(inputTarget.rstrip("/").replace(".git", ""))
            targetDir = os.path.join(cloneBase, repoName)

            if os.path.exists(targetDir):
                logger.info(f"[cloneRepo] Using existing repository: {targetDir}")
            else:
                logger.info(f"[cloneRepo] Cloning repository to: {targetDir}")
                print("\nCloning repository (this may take a moment)...")
                success = cloneRepo(inputTarget, targetDir, githubToken, gitlabToken, bitbucketToken)
                if not success:
                    return False
                logger.info(f"[cloneRepo] Repository available at: {targetDir}")
        else:
            if not os.path.exists(inputTarget):
                logger.error(f"[runAnalysis] Path does not exist: {inputTarget}")
                return False

        # Strict isolation: ensure we are at the repo root or targeting a directory
        if not os.path.isdir(targetDir):
            logger.error(f"[runAnalysis] Target is not a directory: {targetDir}")
            return False

        # Smart Git root discovery
        gitDir = os.path.join(targetDir, ".git")
        if not os.path.isdir(gitDir):
            try:
                for d in os.listdir(targetDir):
                    sub = os.path.join(targetDir, d)
                    if os.path.isdir(sub) and os.path.isdir(os.path.join(sub, ".git")):
                        logger.info(f"[runAnalysis] Found nested .git in: {sub}")
                        targetDir = sub
                        gitDir = os.path.join(targetDir, ".git")
                        break
            except Exception:
                pass

        isGit = os.path.isdir(gitDir)

        report = {
            "repo": os.path.basename(targetDir),
            "target_path": targetDir,
            "mode": args.mode,
            "is_git": isGit,
            "ground_truth": {},
            "tool_metrics": {},
            "heuristics": {},
            "performance": {}
        }
        fileList = []

        print("[Stage 0] Extracting git metadata...")
        s0Start = time.time()
        gitData = runStage0GitAnalysis(targetDir)
        report["ground_truth"]["git"] = {
            "commit_count": gitData.get("commit_count", 0),
            "unique_contributors": gitData.get("unique_contributors", 0),
            "branch_count": gitData.get("branch_count", 0),
            "meaningful_commit_count": gitData.get("meaningful_commit_count", 0),
            "last_update": gitData.get("last_update"),
            "first_commit": gitData.get("first_commit_date"),
            "active_span_months": gitData.get("active_span_months", 0),
            "history_integrity": gitData.get("history_integrity", {}),
            "all_contributors": gitData.get("all_contributors", []),
            "all_contributors_count": len(gitData.get("all_contributors", [])),
            "source": "git"
        }
        report["performance"]["stage0_seconds"] = round(time.time() - s0Start, 2)

        # Calculate OS-safe outDir matching saveReport logic
        repoName = os.path.basename(os.path.abspath(targetDir))
        if isUrl:
            safeName = inputTarget.rstrip("/").split("/")[-1].replace(".git", "")
            prOutDir = os.path.join(args.output_dir, safeName)
        else:
            prOutDir = os.path.join(args.output_dir, repoName)

        gitlab_info = extractGitLabRepoInfo(targetDir, inputTarget)
        bitbucket_info = None if gitlab_info else extractBitbucketRepoInfo(targetDir, inputTarget)

        if gitlab_info:
            print("[Stage 0.5] Running GitLab MR analytics...")
            s05Start = time.time()
            prMetrics = runGitLabMrAnalysis(targetDir, inputTarget, gitlabToken, prOutDir)
            report["github_prs"] = prMetrics
            report["performance"]["stage0_5_seconds"] = round(time.time() - s05Start, 2)
        elif bitbucket_info:
            print("[Stage 0.5] Running Bitbucket PR analytics...")
            s05Start = time.time()
            prMetrics = runBitbucketPrAnalysis(targetDir, inputTarget, bitbucketToken, prOutDir)
            report["github_prs"] = prMetrics
            report["performance"]["stage0_5_seconds"] = round(time.time() - s05Start, 2)
        else:
            print("[Stage 0.5] Running GitHub PR analytics...")
            s05Start = time.time()
            prMetrics = runGitHubPrAnalysis(targetDir, inputTarget, githubToken, prOutDir)
            report["github_prs"] = prMetrics

        print("[Stage 1] Scanning repository structure and signals...")
        stage1Data, fileList = runStage1Analysis(targetDir, maxFiles=args.max_files)
        report["heuristics"]["signals"] = stage1Data.get("signals", {})
        report["heuristics"]["structure"] = stage1Data.get("structure", {})

        print("[Layer 1] Fetching ground truth via cloc...")
        clocData = runCloc(targetDir, fileList=fileList)
        if clocData and "SUM" in clocData:
            report["ground_truth"]["loc"] = {
                "value": clocData["SUM"]["code"],
                "breakdown": {
                    "code": clocData["SUM"]["code"],
                    "comment": clocData["SUM"]["comment"],
                    "blank": clocData["SUM"]["blank"],
                    "nFiles": clocData["SUM"]["nFiles"]
                },
                "source": "cloc"
            }

            # Map cloc breakdown to languages
            langBreakdown = {}
            for lang, stats in clocData.items():
                if lang in ("SUM", "header"):
                    continue
                sum_code = clocData["SUM"]["code"]
                langBreakdown[lang] = {
                    "loc": stats["code"],
                    "files": stats["nFiles"],
                    "pct": round((stats["code"] / sum_code * 100), 2) if sum_code > 0 else 0
                }

            # Differentiate between core (code) and total (all) languages
            coreLangs = [lang for lang in langBreakdown.keys() if lang not in NON_CORE_FORMATS]

            report["ground_truth"]["languages"] = {
                "total_count": len(langBreakdown),
                "core_count": len(coreLangs),
                "core_list": coreLangs,
                "list": list(langBreakdown.keys()),
                "breakdown": langBreakdown,
                "source": "cloc"
            }
        else:
            logger.warning("[Layer 1] Cloc failed or returned empty. Falling back to heuristic scan.")
            report["ground_truth"]["loc"] = {"value": 0, "source": "none", "notes": "cloc failure"}
            report["ground_truth"]["languages"] = {
                "count": 0,
                "list": [],
                "breakdown": {},
                "source": "none"
            }


        loc_val = report["ground_truth"].get("loc", {}).get("value", 0)

        if args.mode in {"stage2", "full"}:
            print("[Stage 2] Running deep analysis (Tokens & Duplication)...")
            s2Start = time.time()

            stage2Result, fileStats, fileTokensMap, framework_findings = runStage2Analysis(fileList)
            report["tool_metrics"]["tokens"] = {
                "llm": stage2Result.get("metrics", {}).get("llm_tokens", {}).get("total", 0),
                "lexical": stage2Result.get("metrics", {}).get("lexical_tokens", {}).get("total", 0)
            }
            report["tool_metrics"]["duplication"] = stage2Result.get("duplication_metrics", {})
            report["performance"]["stage2_seconds"] = round(time.time() - s2Start, 2)

            print("[Layer 3] Computing heuristic scores (AI detection, Rating)...")
            aiData = runAiDetectionAnalysis(
                fileStats,
                report["tool_metrics"]["duplication"],
                fileTokensMap,
            )
            report["heuristics"]["ai_detection"] = aiData

            languages = report.get("ground_truth", {}).get("languages", {}).get("list", [])
            manifestFws = detectFrameworks(targetDir)
            hasJs = any(lang in ["JavaScript", "TypeScript"] for lang in languages)

            hasPackageJson = os.path.exists(os.path.join(targetDir, "package.json"))

            nodeRuntime = []
            if hasJs and hasPackageJson:
                nodeRuntime.append("Node.js")
            finalFws = manifestFws
            for k, v in framework_findings.items():
                if k not in finalFws:
                    finalFws[k] = []
                finalFws[k].extend(v)
                finalFws[k] = list(set(finalFws[k]))

            report["heuristics"]["frameworks"] = finalFws
            report["heuristics"]["runtime"] = nodeRuntime
            report["tool_metrics"]["compliance"] = detectOpenSource(targetDir)

            # Infrastructure, Testing & Documentation
            report["heuristics"]["infrastructure"] = detectInfrastructure(targetDir)
            report["heuristics"]["byte_breakdown"] = calculateByteBreakdown(targetDir)
            report["heuristics"]["docs"] = analyzeDocumentation(targetDir)
            report["heuristics"]["testing"] = {
                "coverage": detectCoverage(targetDir),
                "case_count": estimateTestCases(fileList)
            }
            report["heuristics"]["complexity"] = computeComplexityScore(
                loc_val,
                report["tool_metrics"]["tokens"]["llm"],
                len(fileList)
            )
            report["tool_metrics"]["code_age"] = analyzeCodeAge(targetDir, fileList)
            ratingData = computeRepoRating(
                gitData,
                report["heuristics"],
                report["ground_truth"],  # Pass full ground truth
                report["ground_truth"]["languages"],
            )
            report["heuristics"]["repo_rating"] = ratingData
            report["performance"]["stage4_seconds"] = round(time.time() - s2Start, 2)

        if args.mode in {"full"}:
            print("[Stage 5] Scanning for secrets...")
            s5Start = time.time()
            secretData = scanSecrets(fileList)
            report["tool_metrics"]["secrets"] = secretData
            report["performance"]["stage5_seconds"] = round(time.time() - s5Start, 2)

        if isUrl:
            gitlab_info = extractGitLabRepoInfo(targetDir, inputTarget)
            if gitlab_info:
                domain, project_path, _ = gitlab_info
                print("[GitLab API] Enriching via GitLab REST API...")
                glData = enrichViaGitLabApi(domain, project_path, gitlabToken)
                report["github_api"] = glData
            elif bitbucket_info:
                domain, workspace, repo, is_cloud = bitbucket_info
                print("[Bitbucket API] Enriching via Bitbucket REST API...")
                bbData = enrichViaBitbucketApi(domain, workspace, repo, bitbucketToken, is_cloud)
                report["github_api"] = bbData
            elif githubToken or HAS_PYGITHUB:
                print("[GitHub API] Enriching via GitHub REST API...")
                ghData = enrichViaGithubApi(inputTarget, githubToken)
                report["github_api"] = ghData

        report["performance"]["total_seconds"] = round(time.time() - startTime, 2)
        outFile = saveReport(report, targetDir, inputTarget, args.output_dir, isUrl)

        gt = report.get("ground_truth", {})
        tm = report.get("tool_metrics", {})
        he = report.get("heuristics", {})

        loc_val = gt.get("loc", {}).get("value", 0)
        llm_tok = tm.get("tokens", {}).get("llm", 0)
        commits = gt.get("git", {}).get("commit_count", "N/A")
        persons = gt.get("git", {}).get("unique_contributors", "N/A")
        all_persons = gt.get("git", {}).get("all_contributors_count", "N/A")
        all_persons_list = gt.get("git", {}).get("all_contributors", [])
        all_persons_names = ", ".join([c.get("name", "") for c in all_persons_list])
        if len(all_persons_names) > 120:
            all_persons_names = all_persons_names[:117] + "..."
        langs = gt.get("languages", {}).get("list", [])
        rating = he.get("repo_rating", {})
        dup_pct = tm.get("duplication", {}).get("token_weighted", 0.0) * 100
        sec_count = tm.get("secrets", {}).get("findings_count", 0)
        is_os = tm.get("compliance", {}).get("is_open_source", "N/A")
        git_ok = gt.get("git", {}).get("history_integrity", {}).get("appears_intact", "N/A")

        prMetrics = report.get("github_prs", {})
        pr_avail = prMetrics.get("github_pr_analysis_available", False)
        pr_total = prMetrics.get("total_pr_count", "N/A") if pr_avail else "N/A"
        pr_open = prMetrics.get("open_pr_count", "N/A") if pr_avail else "N/A"
        pr_closed = prMetrics.get("closed_pr_count", "N/A") if pr_avail else "N/A"
        pr_merged = prMetrics.get("merged_pr_count", "N/A") if pr_avail else "N/A"

        primaryLangs = [lang for lang in langs if lang not in NON_CORE_FORMATS]

        print("\n" + "=" * 44)
        print("  Analysis Complete — v3.0.0 (Production Refactor)")
        print("=" * 44)
        print(f"  LOC (Verified)   : {loc_val:,}")
        print(f"  LLM Tokens       : {llm_tok:,}")
        print(f"  Commits          : {commits}")
        print(f"  Contributors (U) : {persons}")
        print(f"  Contributors (All): {all_persons}")
        print(f"  All Contrib Names: {all_persons_names}")
        print(f"  Languages        : {', '.join(primaryLangs[:5])}")
        print(f"  Rating           : {rating.get('rating', 'N/A')} / 10  ({rating.get('label', '')})")
        print(f"  Duplication      : {dup_pct:.1f}% (token-weighted)")
        print(f"  Secrets found    : {sec_count}")
        print(f"  Open Source      : {is_os}")
        print(f"  Git History OK   : {git_ok}")
        if gitlab_info:
            pr_label = "GitLab MRs"
        elif bitbucket_info:
            pr_label = "Bitbucket PRs"
        else:
            pr_label = "GitHub PRs"
        print(f"  {pr_label} (Tot) : {pr_total}")
        print(f"  {pr_label} (Opn) : {pr_open}")
        print(f"  {pr_label} (Cld) : {pr_closed}")
        print(f"  {pr_label} (Mrg) : {pr_merged}")
        print(f"\n  Report saved to  :\n  {outFile}")
        if isUrl:
            print(f"  Repo cloned to   :\n  {targetDir}")
        print("=" * 44)

        return True

    except KeyboardInterrupt:
        print("\n[!] Pipeline interrupted safely.")
        return False


def main():
    """Main entry point for the repository analysis tool."""
    dotenv_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        if key and key not in os.environ:
                            os.environ[key] = val
        except Exception as e:
            print(f"[!] Warning: Failed to load .env file: {e}")

    parser = argparse.ArgumentParser(
        description="Repository Intelligence CLI Tool— Multi-stage code analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Analyse a local repo (full pipeline):
    python Repo_analysis_tool.py -i ./my-project

  Analyse a remote GitHub repo:
    python Repo_analysis_tool.py -i https://github.com/user/repo.git

  Analyse a private repo with GitHub API enrichment:
    python Repo_analysis_tool.py -i https://github.com/org/private-repo.git --github-token ghp_xxx

  Batch-analyse from a text file (one URL/path per line):
    python Repo_analysis_tool.py --batch repos.txt -o ./reports

  Stage 1 structure-only pre-check:
    python Repo_analysis_tool.py -i ./my-app --mode stage1
        """,
    )
    parser.add_argument("--input",  "-i", type=str, help="Local folder path OR Git repository URL")
    parser.add_argument("--output-dir", "-o", type=str, default="./outputs",
                        help="Output directory for reports (default: ./outputs)")
    parser.add_argument("--clone-dir", "-c", type=str, default=None,
                        help="Base directory for cloned repositories (default: ./cloned_repos)")
    parser.add_argument("--mode", "-m", choices=["stage1", "stage2", "full"], default="full",
                        help="Pipeline mode: stage1=structure, stage2=deep, full=all (default: full)")
    parser.add_argument("--max-files", type=int, default=None,
                        help="Soft cap on number of files processed")
    parser.add_argument("--github-token", type=str, default=None,
                        help="GitHub Personal Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--gitlab-token", type=str, default=None,
                        help="GitLab Personal Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--bitbucket-token", type=str, default=None,
                    help="Bitbucket App Password / Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--batch", type=str, default=None,
                        help="Path to a text file containing one repo URL or local path per line")

    if len(sys.argv) == 1:
        while True:
            args = runInteractiveMode()
            if not args:
                break

            runAnalysis(args)

            print("\nWhat would you like to do next?")
            print("1. Analyze another repository")
            print("2. Exit")
            nextAction = input("\nSelect an option (or press Enter to continue): ").strip()

            if nextAction not in ("", "1"):
                break
    else:
        args = parser.parse_args()
        if args.input:
            args.input = args.input.strip().strip('"')

        if args.batch:
            if not os.path.isfile(args.batch):
                print(f"[!] Batch file not found: {args.batch}")
                sys.exit(1)
            with open(args.batch, "r", encoding="utf-8") as f:
                targets = [line.strip().strip('"') for line in f if line.strip() and not line.startswith("#")]
            print(f"[Batch] Processing {len(targets)} repositories...")
            success_count = 0
            for idx, target in enumerate(targets, 1):
                print(f"\n[Batch {idx}/{len(targets)}] {target}")
                batch_args = argparse.Namespace(
                    input=target,
                    output_dir=args.output_dir,
                    clone_dir=args.clone_dir,
                    mode=args.mode,
                    max_files=args.max_files,
                    github_token=args.github_token,
                    gitlab_token=args.gitlab_token,
                    bitbucket_token=args.bitbucket_token,
                )
                if runAnalysis(batch_args):
                    success_count += 1
            print(f"\n[Batch] Completed: {success_count}/{len(targets)} succeeded.")
            print(f"[Batch] Summary CSV: {os.path.join(args.output_dir, 'summary_all.csv')}")

        elif args.input:
            runAnalysis(args)

        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
