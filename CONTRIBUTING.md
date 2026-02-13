Contributing to gitship
Thanks for your interest in contributing to gitship!
Development Setup

Clone the repository:

bashgit clone https://github.com/1minds3t/gitship.git
cd gitship

Create a virtual environment:

bashpython3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

Install in development mode with dev dependencies:

bashpip install -e ".[dev]"

Run tests:

bashpytest

Format code:

bashblack src/
ruff check src/
Project Structure
gitship/
├── src/
│   └── gitship/
│       ├── __init__.py
│       ├── checkgit.py    # Interactive commit inspector
│       └── fixgit.py      # Selective file restorer
├── tests/
│   └── test_basic.py
├── docs/
├── pyproject.toml
├── README.md
└── LICENSE
Adding New Features

Create a new branch: git checkout -b feature/your-feature-name
Make your changes
Add tests for new functionality
Ensure tests pass: pytest
Format code: black src/ and ruff check src/
Commit with clear messages
Push and create a pull request

Code Style

Follow PEP 8
Use type hints where appropriate
Add docstrings to functions and classes
Keep functions focused and small
Write tests for new functionality

Future Feature Ideas
We're planning to add:

AI-powered commit messages: Use LLMs to generate structured commit messages
Batch operations: Handle multiple commits at once
History visualization: Graph-based representation of repository history
Smart auto-commit batching: Intelligent grouping of related changes
MCP integration: Model Context Protocol support for LLM features

Feel free to work on any of these or propose your own ideas!
Questions?
Open an issue on GitHub or reach out to the maintainers.