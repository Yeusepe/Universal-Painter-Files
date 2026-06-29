#!/usr/bin/env python3
"""
Config Manager Module - Load and manage configuration for SPP downgrade tool.

This module provides functionality to:
1. Load configuration from YAML files
2. Provide defaults when no config file is present
3. Get removal rules for specific datasets
4. Validate configuration

Configuration supports:
- Dict removal settings (blacklist, whitelist, regex, combined)
- Per-dataset rules
- Safety settings (backup, dry-run)
- Target version settings
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .dict_remover import RemovalRule

# yaml is optional; load_config falls back to json (imported locally where used).
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class SafetyConfig:
    """Safety-related configuration."""
    create_backup: bool = True
    backup_suffix: str = ".backup"
    dry_run: bool = False


@dataclass
class TargetVersionConfig:
    """Target version configuration."""
    max_data_version: int = 81  # Default for Painter 10.0.0


@dataclass
class DatasetRule:
    """Per-dataset removal rule configuration."""
    strategy: str           # "blacklist", "whitelist", "regex"
    types: List[str] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)


@dataclass
class DictRemovalConfig:
    """Dict removal configuration."""
    enabled: bool = True
    strategy: str = "blacklist"     # "blacklist", "whitelist", "regex", "combined"
    blacklist: List[str] = field(default_factory=list)
    whitelist: List[str] = field(default_factory=list)
    regex_patterns: List[str] = field(default_factory=list)
    combined_mode: str = "OR"       # "AND" or "OR"
    combined_strategies: List[Dict] = field(default_factory=list)
    dataset_rules: Dict[str, DatasetRule] = field(default_factory=dict)


@dataclass
class Config:
    """Complete configuration for SPP downgrade tool."""
    dict_removal: DictRemovalConfig = field(default_factory=DictRemovalConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    target_version: TargetVersionConfig = field(default_factory=TargetVersionConfig)

    def __post_init__(self):
        """Ensure nested configs are properly initialized."""
        if isinstance(self.dict_removal, dict):
            self.dict_removal = DictRemovalConfig(**self.dict_removal)
        if isinstance(self.safety, dict):
            self.safety = SafetyConfig(**self.safety)
        if isinstance(self.target_version, dict):
            self.target_version = TargetVersionConfig(**self.target_version)


def get_default_config() -> Config:
    """
    Get the default configuration.

    Returns:
        Config object with default values
    """
    return Config(
        dict_removal=DictRemovalConfig(
            enabled=True,
            strategy="blacklist",
            blacklist=[
                "BakingCommonParameters",  # Known incompatible type
                "DataTweakInt",            # Only RTTI in Painter 10, not in schema
                "DataTweakInt2",           # Only RTTI in Painter 10, not in schema
            ],
            whitelist=[],
            regex_patterns=[],
            combined_mode="OR",
            combined_strategies=[],
            dataset_rules={}
        ),
        safety=SafetyConfig(
            create_backup=True,
            backup_suffix=".backup",
            dry_run=False
        ),
        target_version=TargetVersionConfig(
            max_data_version=81  # Painter 10.0.0
        )
    )


def parse_dict_removal_config(data: Dict) -> DictRemovalConfig:
    """Parse dict removal configuration from dictionary."""
    config = DictRemovalConfig()

    if not data:
        return config

    config.enabled = data.get('enabled', True)
    config.strategy = data.get('strategy', 'blacklist')
    config.blacklist = data.get('blacklist', []) or []
    config.whitelist = data.get('whitelist', []) or []
    config.regex_patterns = data.get('regex_patterns', []) or []

    # Parse combined strategy
    combined = data.get('combined', {})
    if combined:
        config.combined_mode = combined.get('mode', 'OR')
        config.combined_strategies = combined.get('strategies', []) or []

    # Parse dataset-specific rules
    dataset_rules = data.get('dataset_rules', {})
    if dataset_rules:
        for dataset_name, rule_data in dataset_rules.items():
            if isinstance(rule_data, dict):
                config.dataset_rules[dataset_name] = DatasetRule(
                    strategy=rule_data.get('strategy', 'blacklist'),
                    types=rule_data.get('types', []) or [],
                    patterns=rule_data.get('patterns', []) or []
                )

    return config


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from a file or return defaults.

    Args:
        config_path: Path to config file (YAML or JSON)
                    If None, looks for 'downgrade_config.yaml' in current directory

    Returns:
        Config object
    """
    # Default config file name - check config/ directory first, then current directory
    if config_path is None:
        # Try config/ directory first (for organized structure)
        config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
        config_dir = os.path.abspath(config_dir)

        for filename in ['downgrade_config.yaml', 'downgrade_config.yml', 'downgrade_config.json']:
            config_path_candidate = os.path.join(config_dir, filename)
            if os.path.exists(config_path_candidate):
                config_path = config_path_candidate
                break

        # Fall back to current directory
        if config_path is None:
            if os.path.exists('downgrade_config.yaml'):
                config_path = 'downgrade_config.yaml'
            elif os.path.exists('downgrade_config.yml'):
                config_path = 'downgrade_config.yml'
            elif os.path.exists('downgrade_config.json'):
                config_path = 'downgrade_config.json'
            else:
                # Return defaults if no config file found
                return get_default_config()

    if not os.path.exists(config_path):
        print(f"Warning: Config file '{config_path}' not found, using defaults")
        return get_default_config()

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            if HAS_YAML and config_path.endswith(('.yaml', '.yml')):
                data = yaml.safe_load(f)
            else:
                import json
                data = json.load(f)

        if not data:
            return get_default_config()

        # Parse configuration
        config = Config()

        if 'dict_removal' in data:
            config.dict_removal = parse_dict_removal_config(data['dict_removal'])

        if 'safety' in data:
            safety_data = data['safety']
            config.safety = SafetyConfig(
                create_backup=safety_data.get('create_backup', True),
                backup_suffix=safety_data.get('backup_suffix', '.backup'),
                dry_run=safety_data.get('dry_run', False)
            )

        if 'target_version' in data:
            version_data = data['target_version']
            config.target_version = TargetVersionConfig(
                max_data_version=version_data.get('max_data_version', 81)
            )

        return config

    except Exception as e:
        print(f"Warning: Error loading config file: {e}")
        return get_default_config()


def get_removal_rules_for_dataset(
    dataset_name: str,
    config: Config
) -> List[RemovalRule]:
    """
    Get removal rules for a specific dataset.

    Args:
        dataset_name: Name of the dataset
        config: Configuration object

    Returns:
        List of RemovalRule objects to apply
    """
    if not config.dict_removal.enabled:
        return []

    # Check if there are dataset-specific rules
    if dataset_name in config.dict_removal.dataset_rules:
        dataset_rule = config.dict_removal.dataset_rules[dataset_name]

        if dataset_rule.strategy == "blacklist":
            return [RemovalRule("blacklist", dataset_rule.types)]
        elif dataset_rule.strategy == "whitelist":
            return [RemovalRule("whitelist", dataset_rule.types)]
        elif dataset_rule.strategy == "regex":
            return [RemovalRule("regex", dataset_rule.patterns)]

    # Fall back to global rules
    strategy = config.dict_removal.strategy

    if strategy == "blacklist":
        return [RemovalRule("blacklist", config.dict_removal.blacklist)]

    elif strategy == "whitelist":
        return [RemovalRule("whitelist", config.dict_removal.whitelist)]

    elif strategy == "regex":
        return [RemovalRule("regex", config.dict_removal.regex_patterns)]

    elif strategy == "combined":
        rules = []
        for strat in config.dict_removal.combined_strategies:
            strat_type = strat.get('type', 'blacklist')
            if strat_type == 'blacklist':
                rules.append(RemovalRule("blacklist", strat.get('values', [])))
            elif strat_type == 'whitelist':
                rules.append(RemovalRule("whitelist", strat.get('values', [])))
            elif strat_type == 'regex':
                rules.append(RemovalRule("regex", strat.get('patterns', [])))
        return rules

    return []


def validate_config(config: Config) -> tuple:
    """
    Validate a configuration object.

    Args:
        config: Configuration to validate

    Returns:
        Tuple of (is_valid, list of issues)
    """
    issues = []

    # Validate dict removal config
    dr = config.dict_removal

    if dr.strategy not in ['blacklist', 'whitelist', 'regex', 'combined']:
        issues.append(f"Invalid strategy: {dr.strategy}")

    if dr.strategy == 'blacklist' and not dr.blacklist:
        issues.append("Blacklist strategy selected but no blacklist items defined")

    if dr.strategy == 'whitelist' and not dr.whitelist:
        issues.append("Whitelist strategy selected but no whitelist items defined")

    if dr.strategy == 'regex' and not dr.regex_patterns:
        issues.append("Regex strategy selected but no patterns defined")

    if dr.combined_mode not in ['AND', 'OR']:
        issues.append(f"Invalid combined mode: {dr.combined_mode}")

    # Validate target version
    if config.target_version.max_data_version < 0:
        issues.append("max_data_version must be non-negative")

    return len(issues) == 0, issues


def print_config_summary(config: Config) -> None:
    """Print a summary of the configuration."""
    print("\n" + "=" * 60)
    print("CONFIGURATION SUMMARY")
    print("=" * 60)

    dr = config.dict_removal
    print("\nDict Removal:")
    print(f"  Enabled: {dr.enabled}")
    print(f"  Strategy: {dr.strategy}")

    if dr.strategy == 'blacklist' and dr.blacklist:
        print(f"  Blacklist ({len(dr.blacklist)} items):")
        for item in dr.blacklist[:5]:  # Show first 5
            print(f"    - {item}")
        if len(dr.blacklist) > 5:
            print(f"    ... and {len(dr.blacklist) - 5} more")

    if dr.strategy == 'whitelist' and dr.whitelist:
        print(f"  Whitelist ({len(dr.whitelist)} items):")
        for item in dr.whitelist[:5]:
            print(f"    - {item}")
        if len(dr.whitelist) > 5:
            print(f"    ... and {len(dr.whitelist) - 5} more")

    if dr.strategy == 'regex' and dr.regex_patterns:
        print(f"  Regex patterns ({len(dr.regex_patterns)}):")
        for pattern in dr.regex_patterns[:3]:
            print(f"    - {pattern}")

    if dr.dataset_rules:
        print(f"  Dataset-specific rules ({len(dr.dataset_rules)}):")
        for name, rule in list(dr.dataset_rules.items())[:3]:
            print(f"    - {name}: {rule.strategy}")

    print("\nSafety:")
    print(f"  Create backup: {config.safety.create_backup}")
    print(f"  Backup suffix: {config.safety.backup_suffix}")
    print(f"  Dry run: {config.safety.dry_run}")

    print("\nTarget Version:")
    print(f"  Max data version: {config.target_version.max_data_version}")

    print("=" * 60)


# Testing
if __name__ == "__main__":
    print("Config Manager Module - Test Run")
    print("=" * 70)

    # Test default config
    print("\n1. Testing default configuration:")
    default_config = get_default_config()
    print_config_summary(default_config)

    # Validate default config
    is_valid, issues = validate_config(default_config)
    print(f"\nValidation: {'PASSED' if is_valid else 'FAILED'}")
    if issues:
        for issue in issues:
            print(f"  - {issue}")

    # Test loading config
    print("\n2. Testing config loading:")
    config = load_config()
    # Check if config file exists (try config/ directory first)
    config_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    config_file = os.path.join(os.path.abspath(config_dir), 'downgrade_config.yaml')
    config_exists = os.path.exists(config_file) or os.path.exists('downgrade_config.yaml')
    print(f"   Loaded from file: {'yes' if config_exists else 'using defaults'}")

    # Test rule generation
    print("\n3. Testing rule generation:")
    rules = get_removal_rules_for_dataset("baking/baking.ini", config)
    print(f"   Rules for baking/baking.ini: {len(rules)} rule(s)")
    for rule in rules:
        print(f"     - Type: {rule.rule_type}, Values: {rule.values[:3]}...")

    print("\n" + "=" * 70)
    print("Config Manager Module loaded successfully!")
