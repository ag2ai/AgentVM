"""Helpers for calling OSWorld actions via DesktopEnv.

Goal: avoid hard-coding per-action wrappers.

This module builds a dispatch mapping from action name -> callable(**kwargs)
that forwards arguments directly into env.step({"action_type": name, "arguments": kwargs}).

It discovers action names by scanning the repo's `actions/` folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional


def get_repo_root() -> Path:
	"""Best-effort locate the repository root.

	Historically this file lived under `gdpagent/utils/`, so `parent.parent` points
	to `gdpagent/` (not the repo root). We walk upward until we find a folder that
	contains an `actions/` directory.
	"""
	cur = Path(__file__).resolve()
	for parent in [cur.parent, *cur.parents]:
		if (parent / "actions").is_dir():
			return parent
	# Fallback: assume repo_root is 3 levels up from this file.
	return cur.parents[2]


def get_actions_dir(repo_root: Optional[Path] = None) -> Path:
	repo_root = repo_root or get_repo_root()
	return repo_root / "actions"


def list_action_names(actions_dir: Optional[Path] = None) -> list[str]:
	"""List action directory names that look like OSWorld actions."""
	actions_dir = actions_dir or get_actions_dir()
	if not actions_dir.exists():
		return []

	action_names: list[str] = []
	for child in actions_dir.iterdir():
		if not child.is_dir():
			continue
		# Heuristic: action folder contains schema.yaml
		if (child / "schema.yaml").exists():
			action_names.append(child.name)
	return sorted(action_names)


def _normalize_action_output(observation: Any) -> str:
	"""Normalize DesktopEnv step output into a readable string."""
	if not isinstance(observation, dict):
		return str(observation)

	action_output = observation.get("action_output")
	if isinstance(action_output, dict):
		if "output" in action_output:
			out = action_output.get("output")
			if isinstance(out, dict):
				return json.dumps(out, ensure_ascii=False)
			return "" if out is None else str(out)
		return json.dumps(action_output, ensure_ascii=False)

	# Fall back to the whole observation
	return json.dumps(observation, ensure_ascii=False)


def _make_action_caller(env: Any, action_name: str) -> Callable[..., str]:
	def _caller(**kwargs: Any) -> str:
		action_input = {
			"name": action_name,
			"action_type": action_name,
			"arguments": kwargs,
		}
		observation, *_ = env.step(action_input)
		return _normalize_action_output(observation)

	return _caller


def build_action_dispatch(
	env: Any,
	actions_dir: Optional[Path] = None,
	include: Optional[list[str]] = None,
	exclude: Optional[list[str]] = None,
) -> dict[str, Callable[..., str]]:
	"""Build a mapping action_name -> callable(**kwargs)->str.

	- If include is provided, only those action names are added.
	- If exclude is provided, those action names are removed.
	"""
	all_actions = list_action_names(actions_dir=actions_dir)
	if include is not None:
		wanted = set(include)
		all_actions = [a for a in all_actions if a in wanted]
	if exclude:
		blocked = set(exclude)
		all_actions = [a for a in all_actions if a not in blocked]

	return {name: _make_action_caller(env, name) for name in all_actions}
