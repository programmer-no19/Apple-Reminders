# Apple Reminders for Codex

Use Codex to read and manage the local macOS Reminders app through a native MCP bridge.

The plugin runs locally on your Mac. Reminder data is not sent to a third-party cloud service, and the bridge does not open or activate the Reminders window. macOS may show a one-time permission prompt for Reminders access.

## Install from GitHub

```bash
codex plugin marketplace add programmer-no19/apple-reminders
codex plugin install apple-reminders@apple-reminders
```

Restart Codex or start a new task after installing so the plugin is loaded.

## Available actions

- List Reminders lists
- List and search open reminders
- Create reminders with notes, due dates, and priorities
- Update, complete, and delete reminders

## Requirements

- macOS 14 or newer
- Apple Silicon Mac for the bundled native helper
- Python 3 (included with macOS developer tooling or installable separately)
- Reminders access enabled for Codex's local MCP process in System Settings → Privacy & Security → Reminders

The native helper source is included at `plugins/apple-reminders/scripts/native_helper.swift`. Build a replacement on a compatible Mac with:

```bash
swiftc -parse-as-library -framework EventKit \
  -o plugins/apple-reminders/bin/apple-reminders-native \
  plugins/apple-reminders/scripts/native_helper.swift
```

## License

MIT. See [LICENSE](LICENSE).
