"""Declarative step definitions for each deployment phase."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepPrompt:
    """A single input prompt within a setup step."""

    env_var: str
    label: str
    secret: bool = False
    default: str | None = None
    required: bool = True
    help_hint: str = ""


@dataclass(frozen=True)
class SetupStep:
    """One logical step in the setup wizard."""

    id: str
    name: str
    phase: int
    required: bool
    prompts: list[StepPrompt]
    validator: str
    help_text: str = ""
    depends_on: list[str] = field(default_factory=list)

    @property
    def env_vars(self) -> list[str]:
        return [p.env_var for p in self.prompts]


# ---------------------------------------------------------------------------
# Phase 1 — Core
# ---------------------------------------------------------------------------

ANTHROPIC_API = SetupStep(
    id="anthropic_api",
    name="Anthropic API Key",
    phase=1,
    required=True,
    help_text=(
        "Get your API key from https://console.anthropic.com/settings/keys\n"
        "Starts with 'sk-ant-'."
    ),
    prompts=[
        StepPrompt(
            env_var="ANTHROPIC_API_KEY",
            label="Anthropic API key",
            secret=True,
        ),
    ],
    validator="validate_anthropic",
)

DISCORD_BOT = SetupStep(
    id="discord_bot",
    name="Discord Bot Token",
    phase=1,
    required=True,
    help_text=(
        "Create a bot at https://discord.com/developers/applications\n"
        "Enable 'Message Content Intent' under Privileged Gateway Intents."
    ),
    prompts=[
        StepPrompt(
            env_var="DISCORD_BOT_TOKEN",
            label="Discord bot token",
            secret=True,
        ),
    ],
    validator="validate_discord_token",
)

DISCORD_GUILD = SetupStep(
    id="discord_guild",
    name="Discord Server",
    phase=1,
    required=True,
    help_text=(
        "Right-click your Discord server name → Copy Server ID.\n"
        "(Enable Developer Mode in Settings → Advanced if you don't see it.)"
    ),
    depends_on=["discord_bot"],
    prompts=[
        StepPrompt(
            env_var="DISCORD_GUILD_ID",
            label="Discord server (guild) ID",
        ),
    ],
    validator="validate_discord_guild",
)

DISCORD_CHANNELS = SetupStep(
    id="discord_channels",
    name="Discord Channels",
    phase=1,
    required=True,
    help_text=(
        "Right-click each channel → Copy Channel ID.\n"
        "Only the tasks channel is required; others are optional."
    ),
    depends_on=["discord_guild"],
    prompts=[
        StepPrompt(
            env_var="DISCORD_TASKS_CHANNEL_ID",
            label="Tasks channel ID",
            required=True,
        ),
        StepPrompt(
            env_var="DISCORD_DIGEST_CHANNEL_ID",
            label="Digest channel ID",
            required=False,
            help_hint="(optional — for morning/EOD digests)",
        ),
        StepPrompt(
            env_var="DISCORD_AGENTS_CHANNEL_ID",
            label="Agents channel ID",
            required=False,
            help_hint="(optional — for agent activity feed)",
        ),
        StepPrompt(
            env_var="DISCORD_DEBUG_CHANNEL_ID",
            label="Debug channel ID",
            required=False,
            help_hint="(optional — for debug messages)",
        ),
    ],
    validator="validate_discord_channels",
)

STORAGE_PATHS = SetupStep(
    id="storage_paths",
    name="Storage Paths",
    phase=1,
    required=True,
    help_text="Where Donna stores databases, workspaces, logs, and backups.",
    prompts=[
        StepPrompt(env_var="DONNA_DATA_PATH", label="Data root", default="/donna"),
        StepPrompt(env_var="DONNA_DB_PATH", label="Database path", default="/donna/db"),
        StepPrompt(
            env_var="DONNA_WORKSPACE_PATH",
            label="Workspace path",
            default="/donna/workspace",
        ),
        StepPrompt(env_var="DONNA_BACKUP_PATH", label="Backup path", default="/donna/backups"),
        StepPrompt(env_var="DONNA_LOG_PATH", label="Log path", default="/donna/logs"),
    ],
    validator="validate_paths",
)

BUDGET = SetupStep(
    id="budget",
    name="Cost Limits",
    phase=1,
    required=True,
    help_text="Monthly and daily spending limits for Claude API calls.",
    prompts=[
        StepPrompt(
            env_var="DONNA_MONTHLY_BUDGET_USD",
            label="Monthly budget (USD)",
            default="100.00",
        ),
        StepPrompt(
            env_var="DONNA_DAILY_PAUSE_THRESHOLD_USD",
            label="Daily pause threshold (USD)",
            default="20.00",
        ),
    ],
    validator="validate_budget",
)

GRAFANA = SetupStep(
    id="grafana",
    name="Grafana Dashboard",
    phase=1,
    required=True,
    help_text="Password for the Grafana admin dashboard (port 3000).",
    prompts=[
        StepPrompt(
            env_var="GRAFANA_ADMIN_PASSWORD",
            label="Grafana admin password",
            secret=True,
            default="changeme",
        ),
    ],
    validator="validate_grafana_password",
)

# ---------------------------------------------------------------------------
# Phase 2 — Notifications
# ---------------------------------------------------------------------------

TWILIO = SetupStep(
    id="twilio",
    name="Twilio SMS / Voice",
    phase=2,
    required=False,
    help_text=(
        "Get credentials from https://console.twilio.com\n"
        "Phone numbers must be in E.164 format (e.g. +15551234567)."
    ),
    prompts=[
        StepPrompt(env_var="TWILIO_ACCOUNT_SID", label="Account SID"),
        StepPrompt(env_var="TWILIO_AUTH_TOKEN", label="Auth token", secret=True),
        StepPrompt(
            env_var="TWILIO_PHONE_NUMBER",
            label="Twilio phone number (from)",
            help_hint="E.164 format, e.g. +15551234567",
        ),
        StepPrompt(
            env_var="DONNA_USER_PHONE",
            label="Your phone number (to)",
            help_hint="E.164 format, e.g. +15551234567",
        ),
    ],
    validator="validate_twilio",
)

GOOGLE_OAUTH = SetupStep(
    id="google_oauth",
    name="Google OAuth Credentials",
    phase=2,
    required=False,
    help_text=(
        "Download OAuth 2.0 credentials from Google Cloud Console:\n"
        "https://console.cloud.google.com/apis/credentials\n"
        "Save as a JSON file and provide the path."
    ),
    prompts=[
        StepPrompt(
            env_var="GOOGLE_CREDENTIALS_PATH",
            label="Path to Google credentials JSON",
            default="/donna/config/google_credentials.json",
        ),
    ],
    validator="validate_google_creds_file",
)

GOOGLE_CALENDARS = SetupStep(
    id="google_calendars",
    name="Google Calendar IDs",
    phase=2,
    required=False,
    depends_on=["google_oauth"],
    help_text=(
        "Calendar IDs from Google Calendar Settings → Integrate Calendar.\n"
        "Use 'primary' for your main calendar."
    ),
    prompts=[
        StepPrompt(
            env_var="GOOGLE_CALENDAR_PERSONAL_ID",
            label="Personal calendar ID",
            default="primary",
        ),
        StepPrompt(
            env_var="GOOGLE_CALENDAR_WORK_ID",
            label="Work calendar ID",
            required=False,
            help_hint="(optional)",
        ),
        StepPrompt(
            env_var="GOOGLE_CALENDAR_FAMILY_ID",
            label="Family calendar ID",
            required=False,
            help_hint="(optional)",
        ),
    ],
    validator="validate_calendar_ids",
)

SUPABASE = SetupStep(
    id="supabase",
    name="Supabase Cloud Replica",
    phase=2,
    required=False,
    help_text=(
        "Create a free project at https://supabase.com\n"
        "Find keys in Project Settings → API."
    ),
    prompts=[
        StepPrompt(
            env_var="SUPABASE_URL",
            label="Supabase project URL",
            help_hint="https://your-project.supabase.co",
        ),
        StepPrompt(env_var="SUPABASE_ANON_KEY", label="Anon (public) key", secret=True),
        StepPrompt(
            env_var="SUPABASE_SERVICE_ROLE_KEY",
            label="Service role key",
            secret=True,
        ),
    ],
    validator="validate_supabase",
)

VAULT = SetupStep(
    id="vault",
    name="Obsidian Vault (slice 12)",
    phase=2,
    required=False,
    help_text=(
        "Donna-owned markdown vault (see docs/domain/memory-vault.md).\n"
        "The vault root is bind-mounted into the orchestrator and exposed\n"
        "over WebDAV by the donna-vault Caddy service for Obsidian sync.\n"
        "Generate the password hash with:\n"
        "  docker run --rm caddy:2 caddy hash-password -p '<password>'"
    ),
    prompts=[
        StepPrompt(
            env_var="DONNA_VAULT_PATH",
            label="Vault root (host path)",
            default="/donna/vault",
        ),
        StepPrompt(
            env_var="CADDY_VAULT_USER",
            label="WebDAV basic-auth username",
            default="donna",
        ),
        StepPrompt(
            env_var="CADDY_VAULT_PASSWORD_HASH",
            label="WebDAV basic-auth password (bcrypt hash)",
            secret=True,
            help_hint="(output of 'caddy hash-password')",
        ),
    ],
    validator="validate_vault",
)

# ---------------------------------------------------------------------------
# Phase 3 — Local LLM
# ---------------------------------------------------------------------------

OLLAMA_GPU = SetupStep(
    id="ollama_gpu",
    name="Ollama GPU Assignment",
    phase=3,
    required=False,
    help_text=(
        "GPU device ID for Ollama local LLM inference.\n"
        "Run 'nvidia-smi' to see available GPUs and their IDs."
    ),
    prompts=[
        StepPrompt(
            env_var="DONNA_OLLAMA_GPU_ID",
            label="GPU device ID for Ollama",
            default="1",
            help_hint="(e.g. 0, 1)",
        ),
    ],
    validator="validate_nvidia_gpu",
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_STEPS: list[SetupStep] = [
    ANTHROPIC_API,
    DISCORD_BOT,
    DISCORD_GUILD,
    DISCORD_CHANNELS,
    STORAGE_PATHS,
    BUDGET,
    GRAFANA,
    TWILIO,
    GOOGLE_OAUTH,
    GOOGLE_CALENDARS,
    SUPABASE,
    VAULT,
    OLLAMA_GPU,
]

PHASES: dict[int, str] = {
    1: "Core (Claude + Discord)",
    2: "Notifications (Twilio + Google + Supabase)",
    3: "Local LLM (Ollama)",
    4: "Mobile App (Immich-gated access)",
}


def steps_for_phase(phase: int) -> list[SetupStep]:
    """Return all steps up to and including the given phase."""
    return [s for s in ALL_STEPS if s.phase <= phase]
