#!/usr/bin/env python3
"""Local MCP bridge for the macOS Reminders app.

The active data path is a bundled Swift/EventKit helper. No reminder data
leaves the computer, and the bridge never activates the Reminders window.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


JXA_SCRIPT = r'''
function run(argv) {
  const input = JSON.parse(argv[0] || "{}");
  const app = Application("Reminders");

  function fail(message) {
    throw new Error(message);
  }

  function lists() {
    return app.lists();
  }

  function listByName(name) {
    const available = lists();
    if (!name) {
      if (available.length === 0) fail("No Reminders lists are available.");
      return available[0];
    }
    for (const list of available) {
      if (list.name() === name) return list;
    }
    fail("No Reminders list named '" + name + "'.");
  }

  function isoDate(value) {
    if (!value) return null;
    try {
      return new Date(value).toISOString();
    } catch (_) {
      return String(value);
    }
  }

  function reminderData(reminder, listName) {
    return {
      id: reminder.id(),
      name: reminder.name(),
      list_name: listName,
      completed: Boolean(reminder.completed()),
      notes: reminder.body() || "",
      due_date: isoDate(reminder.dueDate()),
      priority: Number(reminder.priority() || 0)
    };
  }

  function remindersInScope(listName) {
    if (listName) return [{ list: listByName(listName), name: listName }];
    return lists().map(list => ({ list: list, name: list.name() }));
  }

  function findReminder(input) {
    const scopes = remindersInScope(input.list_name);
    for (const scope of scopes) {
      for (const reminder of scope.list.reminders()) {
        if (input.reminder_id && reminder.id() === input.reminder_id) {
          return { reminder: reminder, list_name: scope.name };
        }
        if (!input.reminder_id && input.name && reminder.name() === input.name) {
          return { reminder: reminder, list_name: scope.name };
        }
      }
    }
    const label = input.reminder_id ? "id '" + input.reminder_id + "'" : "named '" + input.name + "'";
    fail("Could not find a reminder " + label + ".");
  }

  switch (input.action) {
    case "list_lists":
      return JSON.stringify({ lists: lists().map(list => ({ name: list.name(), id: list.id() })) });

    case "list_reminders": {
      const includeCompleted = Boolean(input.include_completed);
      const query = String(input.query || "").toLowerCase();
      const result = [];
      for (const scope of remindersInScope(input.list_name)) {
        for (const reminder of scope.list.reminders()) {
          const data = reminderData(reminder, scope.name);
          if (!includeCompleted && data.completed) continue;
          if (query && !(data.name + " " + data.notes).toLowerCase().includes(query)) continue;
          result.push(data);
        }
      }
      result.sort((a, b) => (a.due_date || "9999").localeCompare(b.due_date || "9999"));
      return JSON.stringify({ reminders: result.slice(0, Number(input.limit || 200)) });
    }

    case "create_reminder": {
      if (!input.name) fail("A reminder name is required.");
      const list = listByName(input.list_name);
      const reminder = app.Reminder({ name: String(input.name) });
      list.reminders.push(reminder);
      if (input.notes !== undefined) reminder.body = String(input.notes);
      if (input.due_date) reminder.dueDate = new Date(String(input.due_date));
      if (input.priority !== undefined) reminder.priority = Number(input.priority);
      return JSON.stringify({ reminder: reminderData(reminder, list.name()) });
    }

    case "update_reminder": {
      const found = findReminder(input);
      if (input.new_name !== undefined) found.reminder.name = String(input.new_name);
      if (input.notes !== undefined) found.reminder.body = String(input.notes);
      if (input.due_date !== undefined) {
        found.reminder.dueDate = input.due_date ? new Date(String(input.due_date)) : null;
      }
      if (input.priority !== undefined) found.reminder.priority = Number(input.priority);
      return JSON.stringify({ reminder: reminderData(found.reminder, found.list_name) });
    }

    case "complete_reminder": {
      const found = findReminder(input);
      found.reminder.completed = true;
      return JSON.stringify({ reminder: reminderData(found.reminder, found.list_name) });
    }

    case "delete_reminder": {
      const found = findReminder(input);
      app.delete(found.reminder);
      return JSON.stringify({ deleted: true, name: found.reminder.name(), list_name: found.list_name });
    }

    default:
      fail("Unknown Reminders action: " + input.action);
  }
}
'''

NATIVE_HELPER = Path(__file__).resolve().parent.parent / "bin" / "apple-reminders-native"


APPLE_SCRIPT = r'''
on replaceText(findText, replaceText, sourceText)
  set savedDelimiters to AppleScript's text item delimiters
  set AppleScript's text item delimiters to findText
  set textParts to text items of sourceText
  set AppleScript's text item delimiters to replaceText
  set resultText to textParts as text
  set AppleScript's text item delimiters to savedDelimiters
  return resultText
end replaceText

on cleanText(value)
  if value is missing value then return ""
  set textValue to value as text
  set textValue to my replaceText(return, " ", textValue)
  set textValue to my replaceText(linefeed, " ", textValue)
  set textValue to my replaceText(character id 30, " ", textValue)
  set textValue to my replaceText(character id 31, " ", textValue)
  return textValue
end cleanText

on twoDigits(value)
  set textValue to value as text
  if (length of textValue) is 1 then return "0" & textValue
  return textValue
end twoDigits

on isoDate(value)
  if value is missing value then return ""
  set yearText to year of value as text
  set monthText to my twoDigits(month of value as integer)
  set dayText to my twoDigits(day of value)
  set hourText to my twoDigits(hours of value)
  set minuteText to my twoDigits(minutes of value)
  set secondText to my twoDigits(seconds of value)
  return yearText & "-" & monthText & "-" & dayText & "T" & hourText & ":" & minuteText & ":" & secondText
end isoDate

on run argv
  set requestedList to item 1 of argv
  set includeCompleted to item 2 of argv
  set fieldDelimiter to character id 31
  set rowDelimiter to character id 30
  set outputRows to {}

  tell application "Reminders"
    if requestedList is "" then
      set targetLists to every list
    else
      set targetLists to every list whose name is requestedList
    end if

    repeat with currentList in targetLists
      set currentListName to my cleanText(name of currentList)
      repeat with currentReminder in every reminder of currentList
        set isCompleted to completed of currentReminder
        if includeCompleted is "1" or isCompleted is false then
          set completedText to "false"
          if isCompleted then set completedText to "true"
          set reminderBody to body of currentReminder
          if reminderBody is missing value then set reminderBody to ""
          set priorityText to priority of currentReminder as text
          set rowText to my cleanText(id of currentReminder) & fieldDelimiter & my cleanText(name of currentReminder) & fieldDelimiter & completedText & fieldDelimiter & my cleanText(reminderBody) & fieldDelimiter & my isoDate(due date of currentReminder) & fieldDelimiter & priorityText & fieldDelimiter & currentListName
          set end of outputRows to rowText
        end if
      end repeat
    end repeat
  end tell

  set AppleScript's text item delimiters to rowDelimiter
  return outputRows as text
end run
'''


TOOLS = [
    {
        "name": "list_reminder_lists",
        "description": "List the Reminders lists available in the local macOS Reminders app.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_reminders",
        "description": "List open reminders, optionally filtered by list or search text. Set include_completed to include completed reminders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "Exact Reminders list name."},
                "query": {"type": "string", "description": "Text to search in reminder names and notes."},
                "include_completed": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "create_reminder",
        "description": "Create a reminder in the local macOS Reminders app. This changes the user's reminders.",
        "inputSchema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "list_name": {"type": "string", "description": "Exact list name; defaults to the first available list."},
                "notes": {"type": "string"},
                "due_date": {"type": "string", "description": "ISO 8601 date/time, for example 2026-09-21T09:00:00+05:30."},
                "priority": {"type": "integer", "minimum": 0, "maximum": 9, "description": "0 none, 1 high, 5 medium, 9 low in Reminders."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "update_reminder",
        "description": "Update a reminder by exact name or reminder_id. This changes the user's reminders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact current reminder name."},
                "reminder_id": {"type": "string", "description": "Stable Reminders identifier from list_reminders."},
                "list_name": {"type": "string"},
                "new_name": {"type": "string"},
                "notes": {"type": "string"},
                "due_date": {"type": ["string", "null"], "description": "ISO 8601 date/time, or null to clear the due date."},
                "priority": {"type": "integer", "minimum": 0, "maximum": 9},
            },
            "anyOf": [{"required": ["name"]}, {"required": ["reminder_id"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "complete_reminder",
        "description": "Mark a reminder complete by exact name or reminder_id. This changes the user's reminders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "reminder_id": {"type": "string"},
                "list_name": {"type": "string"},
            },
            "anyOf": [{"required": ["name"]}, {"required": ["reminder_id"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_reminder",
        "description": "Delete a reminder by exact name or reminder_id. This is destructive and changes the user's reminders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "reminder_id": {"type": "string"},
                "list_name": {"type": "string"},
            },
            "anyOf": [{"required": ["name"]}, {"required": ["reminder_id"]}],
            "additionalProperties": False,
        },
    },
]


def invoke_reminders(action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = dict(arguments)
    payload["action"] = action
    if not NATIVE_HELPER.exists():
        raise RuntimeError(f"Native Apple Reminders helper is missing: {NATIVE_HELPER}")
    completed = subprocess.run(
        [str(NATIVE_HELPER), json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or "The macOS Reminders bridge failed.")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The native Apple Reminders helper returned invalid JSON.") from exc
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result


def invoke_list_reminders(arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch reminder fields in one AppleScript pass to avoid slow JXA property calls."""
    completed = subprocess.run(
        [
            "osascript",
            "-e",
            APPLE_SCRIPT,
            str(arguments.get("list_name") or ""),
            "1" if arguments.get("include_completed") else "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or "The macOS Reminders bridge failed.")

    rows = []
    raw_output = completed.stdout.rstrip("\r\n")
    for raw_row in raw_output.split(chr(30)) if raw_output else []:
        fields = raw_row.split(chr(31))
        if len(fields) != 7:
            continue
        reminder_id, name, completed_text, notes, due_date, priority, list_name = fields
        rows.append(
            {
                "id": reminder_id,
                "name": name,
                "list_name": list_name,
                "completed": completed_text == "true",
                "notes": notes,
                "due_date": due_date or None,
                "priority": int(priority or 0),
            }
        )

    query = str(arguments.get("query") or "").lower()
    if query:
        rows = [row for row in rows if query in (row["name"] + " " + row["notes"]).lower()]
    rows.sort(key=lambda row: row["due_date"] or "9999")
    limit = min(max(int(arguments.get("limit") or 200), 1), 500)
    return {"reminders": rows[:limit]}


def response(request_id: Any, result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, separators=(",", ":")), flush=True)


def error_response(request_id: Any, code: int, message: str) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, separators=(",", ":")), flush=True)


def tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    action_by_tool = {
        "list_reminder_lists": "list_lists",
        "list_reminders": "list_reminders",
        "create_reminder": "create_reminder",
        "update_reminder": "update_reminder",
        "complete_reminder": "complete_reminder",
        "delete_reminder": "delete_reminder",
    }
    data = invoke_reminders(action_by_tool[name], arguments)
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
        "structuredContent": data,
    }


def main() -> None:
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
            method = request.get("method")
            request_id = request.get("id")

            if method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            if method == "ping":
                response(request_id, {})
                continue
            if method == "initialize":
                requested = request.get("params", {}).get("protocolVersion", "2024-11-05")
                supported = {"2024-11-05", "2025-03-26", "2025-06-18"}
                response(
                    request_id,
                    {
                        "protocolVersion": requested if requested in supported else "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "apple-reminders", "version": "0.1.0"},
                    },
                )
                continue
            if method == "tools/list":
                response(request_id, {"tools": TOOLS})
                continue
            if method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                if name not in {tool["name"] for tool in TOOLS}:
                    error_response(request_id, -32602, f"Unknown tool: {name}")
                    continue
                try:
                    result = tool_result(name, params.get("arguments") or {})
                    response(request_id, result)
                except Exception as exc:  # MCP tool failures should stay in-band.
                    response(
                        request_id,
                        {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                    )
                continue
            if request_id is not None:
                error_response(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            if "request_id" in locals() and request_id is not None:
                error_response(request_id, -32700, str(exc))


if __name__ == "__main__":
    main()
