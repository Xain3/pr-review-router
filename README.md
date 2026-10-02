# pr-review-router

## Overview

`pr-review-router` is a Python project designed to facilitate the review process of pull requests. This project provides a command-line interface (CLI) for users to interact with the functionality related to pull request reviews.

## Features

- Command-line interface for easy interaction.
- Future enhancements planned for pull request integration and additional features.

## Project Structure

The project follows a structured layout to separate different components:

```
pr-review-router
├── src
│   └── pr_review_router
│       ├── __init__.py
│       └── cli.py
├── tests
│   └── test_cli.py
├── docs
│   ├── specification.md
│   ├── implementation-handoff.md
│   └── scaffold-handoff.md
├── examples
│   └── README.md
├── .github
│   └── workflows
│       └── checks.yml
├── .editorconfig
├── .env.example
├── .gitignore
├── .python-version
├── CONTRIBUTING.md
├── pyproject.toml
├── README.md
└── uv.lock
```

## Installation

To install the project, clone the repository and install the dependencies using the following commands:

```bash
git clone <repository-url>
cd pr-review-router
uv sync --locked
```

Ensure you have Python 3.12 or higher installed.

## Usage

To access the command-line interface, use the following commands:

```bash
uv run pr-review-router --help
uv run pr-review-router --version
```

## Contributing

Contributions are welcome! Please refer to the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines on how to contribute to this project.

## License

Licensing is pending owner selection. Please check back later for updates.