"""Generate Inno Setup components and installation code from engine metadata.

The installer offers two types: Default (no optional tools) and Custom, where the
user ticks tools (and a tool's packages, e.g. GetOrganelle's databases)."""
import re


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Invalid installer tool identifier: {value!r}")
    return value


def _label(value):
    return str(value).replace('"', '""')


def render(catalog):
    components = ['Name: "core"; Description: "Cladia application (required)"; Types: default custom; Flags: fixed']
    runs = []
    for engine in catalog:
        engine_id = _identifier(engine["id"])
        choices = engine["choices"]
        estimate = engine.get("estimateBytes")
        size = (f"about {estimate / 1_000_000_000:.1f} GB" if estimate and estimate >= 1_000_000_000
                else f"about {estimate / 1_000_000:.0f} MB" if estimate else "size unavailable")
        description = f'{engine["label"]} ({size})' + (" - choose databases below" if choices else "")
        # These tools download later, so Inno cannot infer their footprint from
        # [Files]. Charge each environment once, on its parent component.
        disk_space = f'; ExtraDiskSpaceRequired: {estimate}' if estimate else ''
        components.append(f'Name: "tool_{engine_id}"; Description: "{_label(description)}"; Types: custom{disk_space}')
        for index, choice in enumerate(choices):
            choice_id = _identifier(choice["value"])
            components.append(f'Name: "tool_{engine_id}\\{choice_id}"; '
                              f'Description: "{_label(choice["label"])} (extra space varies)"; Types: custom')
        runs.append(_run(engine_id, choices, description))
    return "\n".join(components) + "\n", "\n".join(runs) + "\n"


def _run(engine_id, choices, description):
    label = description.replace("'", "''")
    lines = [f"  if WizardIsComponentSelected('tool_{engine_id}') then", "  begin",
             f"    Arguments := '{engine_id}';"]
    for choice in choices:
        choice_id = _identifier(choice["value"])
        lines += [f"    if WizardIsComponentSelected('tool_{engine_id}\\{choice_id}') then",
                  f"      Arguments := Arguments + ' {choice_id}';"]
    lines += [f"    InstallOptionalTool(Arguments, '{label}');", "  end;"]
    return '\n'.join(lines)
