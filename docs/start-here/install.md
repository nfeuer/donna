# Install

The canonical install instructions live at the repo root:

- [`SETUP.md`](https://github.com/nfeuer/donna/blob/main/SETUP.md) — prerequisites, OS setup, Python + Docker, first-boot.
- [`INSTALL_DAY.md`](https://github.com/nfeuer/donna/blob/main/INSTALL_DAY.md) — hour-by-hour playbook for hardware install day.

These files contain many relative cross-references to repo assets, so they
are **linked** rather than embedded here. Open them on GitHub or clone the
repo and read them in place.

## Minimal Quickstart

If you just want to get the code running against the Claude API:

```bash
git clone https://github.com/nfeuer/donna
cd donna
cp docker/.env.example docker/.env     # fill in ANTHROPIC_API_KEY etc.
pip install -e ".[dev]"
alembic upgrade head
donna run --dev
```

Full end-to-end walkthrough: [Quickstart](quickstart.md).

## Recovery

If something goes wrong later, see
[Operations → Backup & Recovery](../operations/backup-recovery.md).
