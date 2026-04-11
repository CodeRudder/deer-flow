"""Subagent registry for managing available subagents."""

import logging
from dataclasses import replace

from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def _load_custom_agents() -> dict[str, SubagentConfig]:
    """Dynamically load Custom Agents (SOUL.md + config.yaml) as Sub-Agent configs.

    Scans the agents directory and converts each Custom Agent into a SubagentConfig
    so the Lead Agent's `task()` tool can delegate to them by name.

    Returns:
        Mapping of agent name -> SubagentConfig for each valid custom agent.
    """
    agents: dict[str, SubagentConfig] = {}
    try:
        from deerflow.config.agents_config import list_custom_agents, load_agent_soul
    except Exception:
        logger.debug("Could not import agents_config for custom agent discovery")
        return agents

    try:
        custom_list = list_custom_agents()
    except Exception:
        logger.debug("Could not list custom agents")
        return agents

    for agent_cfg in custom_list:
        soul = load_agent_soul(agent_cfg.name)
        if not soul or not soul.strip():
            logger.debug("Skipping custom agent '%s': no SOUL.md content", agent_cfg.name)
            continue

        description = agent_cfg.description or f"Custom agent: {agent_cfg.name}"
        system_prompt = f"You are the {agent_cfg.name} agent.\n\n{soul}"

        sub_config = SubagentConfig(
            name=agent_cfg.name,
            description=description,
            system_prompt=system_prompt,
            disallowed_tools=["task", "ask_clarification", "present_files"],
            model="inherit",
            max_turns=100,
            timeout_seconds=900,
        )
        agents[agent_cfg.name] = sub_config
        logger.debug("Loaded custom agent as subagent: %s", agent_cfg.name)

    if agents:
        logger.info("Discovered %d custom agent(s) as subagents: %s", len(agents), list(agents.keys()))

    return agents


def _get_all_subagents() -> dict[str, SubagentConfig]:
    """Merge built-in subagents with dynamically loaded custom agents.

    Custom agents take precedence over built-ins if names collide.

    Returns:
        Combined mapping of all available subagents.
    """
    all_agents = dict(BUILTIN_SUBAGENTS)
    all_agents.update(_load_custom_agents())
    return all_agents


def get_subagent_config(name: str) -> SubagentConfig | None:
    """Get a subagent configuration by name, with config.yaml overrides applied.

    Looks up both built-in subagents and dynamically loaded custom agents.

    Args:
        name: The name of the subagent.

    Returns:
        SubagentConfig if found (with any config.yaml overrides applied), None otherwise.
    """
    all_agents = _get_all_subagents()
    config = all_agents.get(name)
    if config is None:
        return None

    # Apply timeout override from config.yaml (lazy import to avoid circular deps)
    from deerflow.config.subagents_config import get_subagents_app_config

    app_config = get_subagents_app_config()
    effective_timeout = app_config.get_timeout_for(name)
    effective_max_turns = app_config.get_max_turns_for(name, config.max_turns)

    overrides = {}
    if effective_timeout != config.timeout_seconds:
        logger.debug(
            "Subagent '%s': timeout overridden by config.yaml (%ss -> %ss)",
            name,
            config.timeout_seconds,
            effective_timeout,
        )
        overrides["timeout_seconds"] = effective_timeout
    if effective_max_turns != config.max_turns:
        logger.debug(
            "Subagent '%s': max_turns overridden by config.yaml (%s -> %s)",
            name,
            config.max_turns,
            effective_max_turns,
        )
        overrides["max_turns"] = effective_max_turns
    if overrides:
        config = replace(config, **overrides)

    return config


def list_subagents() -> list[SubagentConfig]:
    """List all available subagent configurations (with config.yaml overrides applied).

    Returns:
        List of all registered SubagentConfig instances.
    """
    return [get_subagent_config(name) for name in _get_all_subagents()]


def get_subagent_names() -> list[str]:
    """Get all available subagent names.

    Returns:
        List of subagent names.
    """
    return list(_get_all_subagents().keys())


def get_available_subagent_names() -> list[str]:
    """Get subagent names that should be exposed to the active runtime.

    Returns:
        List of subagent names visible to the current sandbox configuration.
    """
    names = list(_get_all_subagents().keys())
    try:
        host_bash_allowed = is_host_bash_allowed()
    except Exception:
        logger.debug("Could not determine host bash availability; exposing all built-in subagents")
        return names

    if not host_bash_allowed:
        names = [name for name in names if name != "bash"]
    return names
