@echo off
rem Double-click to mark candidate-agent, company-agent and account-agent as trusted in Claude Code.
rem Close Claude Code and Recruit Desk first. See trust-folders.py for details.
uv run --no-project python "%~dp0trust-folders.py"
pause
