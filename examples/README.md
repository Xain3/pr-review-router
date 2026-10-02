# Examples of Using pr-review-router

## Overview

This README provides examples of how to use the `pr-review-router` project. It includes usage instructions and potential configurations to help you get started.

## Usage Instructions

To use the `pr-review-router`, you can run the command line interface (CLI) with the following commands:

1. **Help Command**: To see the available commands and options, run:
   ```
   pr-review-router --help
   ```

2. **Version Command**: To check the current version of the `pr-review-router`, run:
   ```
   pr-review-router --version
   ```

## Example Configurations

### Basic Configuration

You can set up your environment by creating a `.env` file based on the provided `.env.example`. Here’s an example of what your `.env` file might look like:

```
# Optional API Keys
TYPESAFE_API_KEY=your_typesafe_api_key
REVIEW_MODEL_API_KEY=your_review_model_api_key
```

### Running the CLI

To run the CLI with specific commands, you can use the following syntax:

```
uv run pr-review-router [command] [options]
```

Replace `[command]` with the desired command (e.g., `--help`, `--version`) and `[options]` with any additional options you may need.

## Future Examples

As the project develops, additional examples and configurations will be added to this section to demonstrate new features and functionalities. Stay tuned for updates!