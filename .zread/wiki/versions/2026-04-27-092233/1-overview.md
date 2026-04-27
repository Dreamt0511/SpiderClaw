This page introduces the core purpose, key capabilities, and high-level design of SpiderClaw, an event-driven automatic code diagnosis and repair system built for developers to reduce time spent fixing common code errors. It supports integration with code repositories and collaboration tools to deliver end-to-end automated error resolution workflows.
Sources: [pyproject.toml](pyproject.toml#L1-L35)

## Core Value
SpiderClaw eliminates manual repetitive error fixing work for beginner and intermediate developers: it automatically detects code errors (syntax issues, runtime exceptions, CI failures), runs AI-powered root cause analysis, generates validated fix proposals, and sends notifications to your team collaboration tool. It comes with built-in safety guardrails to ensure all proposed fixes do not break existing functionality.
Sources: [pyproject.toml](pyproject.toml#L1-L35), [main.py](main.py#L1-L19)

## Key Features
| Feature | Benefit for Developers | Supported Use Case |
|---------|-------------------------|--------------------|
| Multi-source error monitoring | No need to manually check for errors across different systems | GitHub webhook events, local test run errors, CI pipeline failures |
| Agent-based automatic repair | Get production-ready fixes without manual debugging | Syntax errors, common runtime exceptions, dependency compatibility issues |
| Feishu/Lark notification integration | Stay updated on repair progress without switching tools | Fix status alerts, fix proposal review requests, repair failure notifications |
| Built-in safety guardrails | Avoid unintended changes from automatic fixes | Dry run validation, dependency check, syntax verification before applying fixes |
| Extensible agent system | Customize repair logic for your team's specific tech stack | Custom error type support, custom fix rules, team-specific prompt templates |
Sources: [pyproject.toml](pyproject.toml#L1-L35), [src/agent/orchestrator.py](src/agent/orchestrator.py), [src/monitor/webhook_server.py](src/monitor/webhook_server.py), [src/notify/lark_notify.py](src/notify/lark_notify.py)

## High-Level Architecture
The system follows a modular event-driven design, with clear separation of responsibilities across subsystems:
```mermaid
flowchart LR
    A[Error Source] --> B[Monitor Subsystem]
    B --> C[Event Bus]
    C --> D[Agent Orchestrator]
    D --> E[Safety Guardrail Module]
    E --> F[Fix Application]
    D --> G[Notification Subsystem]
    G --> H[Collaboration Tool (Feishu/Lark/GitHub)]
```
All components communicate via the central event bus to ensure loose coupling and easy extensibility for custom functionality.
Sources: [src/bus/event_bus.py](src/bus/event_bus.py), [src/entry.py](src/entry.py#L1-L17)

## Core Project Structure
SpiderClaw uses a clean, standardized directory layout for easy navigation:
```
.
├── src/                # Core source code
│   ├── agent/          # AI agent orchestration and subagents
│   ├── bus/            # Central event bus implementation
│   ├── cli/            # Command line interface tools
│   ├── config/         # System configuration management
│   ├── monitor/        # Error event monitoring (webhook, local)
│   ├── notify/         # Third-party notification integration
│   └── safety/         # Fix validation safety guardrails
├── config/             # User configuration files
├── docs/               # Official documentation
├── local_test/         # Local test scripts for debugging functionality
└── tests/              # Unit and integration test suite
```
You can run the system via the `spiderclaw` CLI command (configured as part of installation) or directly via `python main.py` from the project root.
Sources: [pyproject.toml](pyproject.toml#L27-L28), [main.py](main.py#L1-L19)

## Next Steps
If you are new to SpiderClaw, follow this recommended reading order to get started:
1. Complete system setup: [Installation Guide](3-installation-guide)
2. Run your first auto-fix test: [Quick Start](2-quick-start)
3. Configure your instance for your use case: [Basic Configuration](4-basic-configuration)
4. Set up integrations: [GitHub Webhook Configuration](6-github-webhook-configuration), [Feishu/Lark Notification Setup](7-feishu-lark-notification-setup)
5. See the full list of supported error types: [Supported Error Types Reference](5-supported-error-types-reference)