# dowbench plugin example

A minimal, standalone package showing how to add a **defense** and a **provider** to dowbench
from outside its source tree — no fork, no edit to the core (ADR 0021).

It declares two entry points in its own `pyproject.toml`:

```toml
[project.entry-points."dowbench.defenses"]
head_cap = "dowbench_plugin_example.defense:HeadCap"

[project.entry-points."dowbench.providers"]
echo = "dowbench_plugin_example.provider:build_echo"
```

Install it next to dowbench (`pip install -e examples/plugin_example`) and the new names are
usable in a config immediately:

```yaml
provider: echo
defenses:
  - name: none
  - name: head_cap
    params: {max_chars: 500}
```

- A **defense** is any subclass of `dowbench.defenses.base.Defense` with a class-level `name`
  and any of the `before_call` / `after_call` / `on_tool_result` hooks.
- A **provider** entry point is a factory `build(config, dataset) -> Provider` returning an
  object that satisfies `dowbench.providers.base.Provider`. If it needs offline replay or
  agent-run metering it also registers a `dowbench.codecs` / `dowbench.sut_mappers` entry.

The same pattern adds attacks via a `dowbench.attacks` entry point returning
`{"benign": [...], "attacks": [...]}`.
