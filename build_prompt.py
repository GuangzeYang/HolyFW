#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from common import build_role_task_prompt
from pathlib import Path

domain_resource_path = Path("domain_resource.md")
domain_context = ""
if domain_resource_path.exists():
    with open(domain_resource_path, encoding="utf-8") as f:
        domain_context = f.read()

prompt = build_role_task_prompt(domain_context, min_tasks_per_role=18, roles=["hr", "accountancy", "manager", "programmer"])
print(prompt)