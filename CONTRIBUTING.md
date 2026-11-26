# Contributing

For a bug report, include the application build, operating system, steps to reproduce, and what you expected. Use a fictional manuscript from [examples](examples/) or a short synthetic reproducer. Keep manuscripts, export files, model credentials, and session data out of issue attachments and test fixtures.

Run the checks from the repository root.

```sh
uv sync --locked --group dev --extra web --extra mcp
uv run --locked --group dev --extra web --extra mcp pytest -q
```

For desktop changes, verify the affected workflow with the installed application. See [macOS builds](docs/macos_desktop.md), [Windows builds](docs/windows_desktop.md), and the [workbench verification guide](docs/workbench-testing.md).
