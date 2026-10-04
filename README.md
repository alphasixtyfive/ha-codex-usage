<img src="https://raw.githubusercontent.com/alphasixtyfive/ha-codex-usage/main/custom_components/codex_usage/brand/icon%402x.png" width="96" height="96" alt="Codex usage icon">

# Codex usage

Your Codex allowance and reset time, quietly available in Home Assistant.

Home Assistant reads your account directly every five minutes. Two sensors, one shared request, and a separate sign-in that renews automatically. The laptop may have the evening off.

**Version 0.1.0** · Home Assistant 2026.3 or later · MIT licence

This is an unofficial community integration, independent of OpenAI. The icon is original artwork.

## Installation

### HACS

In HACS, open **Custom repositories**, add `https://github.com/alphasixtyfive/ha-codex-usage` with the category **Integration**, then download **Codex usage** and restart Home Assistant. This repository is available as a custom repository; it is not included in the default HACS catalogue.

### Manual

Download [the latest release](https://github.com/alphasixtyfive/ha-codex-usage/releases/latest). Copy `custom_components/codex_usage` into your Home Assistant configuration directory, then restart Home Assistant.

## Sign in

1. In [ChatGPT Security Settings](https://chatgpt.com/#settings/Security), enable **device code sign-in for Codex, Excel, PowerPoint, and Word**.
2. In Home Assistant, open **Settings → Devices & services → Add integration → Codex usage**.
3. Continue to obtain a code. Open the sign-in link shown in the form and enter that code using the ChatGPT account you want to monitor.
4. After approving the sign-in, return to Home Assistant and select **Submit**.

Use the code shown by Home Assistant. A code from `codex login --device-auth` authorizes the CLI instead. Codes expire after 15 minutes; submitting an expired code produces a fresh one. If approval is still pending, finish signing in and submit again.

Home Assistant gets its own session. There is no need to copy tokens from a desktop installation, supply an API key, or keep another computer running. One account is supported per Home Assistant installation.

## Sensors

| Default entity ID | What it shows |
| --- | --- |
| `sensor.codex_usage_remaining` | Remaining account allowance, as a percentage. |
| `sensor.codex_usage_reset` | The corresponding reset time, displayed in your local time zone. |

When several windows are reported, the percentage is the **lowest remaining allowance** and the reset time belongs to that same window. For example, if five-hour usage has 80% left and weekly usage has 25% left, the sensors show 25% and the weekly reset time. Ties favour the shorter window.

The percentage sensor also has `window_minutes` and `plan` attributes. This measures the account allowance, rather than a count of unused model tokens. Separate model buckets, code review allowances, and purchased credits are excluded. Missing usage is unavailable; a missing reset time is unknown.

For a simple dashboard card:

```yaml
type: entities
title: Codex usage
show_header_toggle: false
entities:
  - entity: sensor.codex_usage_remaining
    name: Remaining
  - entity: sensor.codex_usage_reset
    name: Resets
    time_format: datetime
```

Entity IDs may differ if those names are already in use or you rename the device.

## How it behaves

Both sensors share a five-minute poll. Unchanged readings do not force new state updates. Connection failures make the sensors unavailable; recovery restores them. Login credentials renew automatically, and a revoked session starts Home Assistant's reauthentication flow.

Credentials live in Home Assistant's config entry and replacement refresh tokens are saved automatically. They are not exposed as sensor attributes. Protect your Home Assistant backups accordingly, and never attach credentials or the contents of `.storage` to an issue.

The integration calls authentication endpoints and `https://chatgpt.com/backend-api/wham/usage`. It does not run models, read conversations, or redeem usage resets. The usage endpoint is **not a documented stable public API**: changes at OpenAI may require an integration update.

The sign-in approach follows OpenAI's [account authentication guidance](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions) and [device authentication documentation](https://learn.chatgpt.com/docs/cli/reference#codex-login).

## Development

Use Python 3.14.2 or later in a virtual environment, then run:

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check custom_components tests
python -m ruff format --check custom_components tests
python -m unittest discover -s tests/codex_usage
```

The tests cover response parsing, device sign-in, retry behaviour, and login renewal without real credentials. The lifecycle tests require Home Assistant and are skipped if it is absent; CI installs it and runs the complete suite against the minimum supported release and the current tested release.

Bug reports and small, well-explained improvements are welcome. Please include your Home Assistant version and the relevant error, with account details removed.
