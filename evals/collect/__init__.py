"""Collecting the detection set (Component 6, part C): candidate scans, outcome labels, the split.

Everything here reads the chain and writes label files; nothing here runs the agent. The written
rules are in `evals/datasets/label_rules.toml` and `rules.py` is the only code that reads them.
"""
