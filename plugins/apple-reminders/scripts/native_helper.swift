import EventKit
import Foundation

enum NativeError: LocalizedError {
  case invalidInput
  case accessDenied
  case listNotFound(String)
  case reminderNotFound(String)
  case invalidDate(String)
  case saveFailed

  var errorDescription: String? {
    switch self {
    case .invalidInput:
      return "Invalid Apple Reminders request."
    case .accessDenied:
      return "Access to Apple Reminders was denied. Allow it in System Settings > Privacy & Security > Reminders."
    case .listNotFound(let name):
      return "No Reminders list named '\(name)'."
    case .reminderNotFound(let name):
      return "Could not find a reminder named or identified as '\(name)'."
    case .invalidDate(let value):
      return "Invalid due date: \(value). Use ISO 8601, such as 2026-09-21T09:00:00+05:30, or YYYY-MM-DD."
    case .saveFailed:
      return "macOS could not save the reminder."
    }
  }
}

struct NativeHelper {
  let store = EKEventStore()

  func run(input: [String: Any]) async throws -> [String: Any] {
    let granted: Bool
    if #available(macOS 14.0, *) {
      granted = try await store.requestFullAccessToReminders()
    } else {
      granted = try await store.requestAccess(to: .reminder)
    }
    guard granted else { throw NativeError.accessDenied }

    guard let action = input["action"] as? String else {
      throw NativeError.invalidInput
    }

    switch action {
    case "list_lists":
      return listLists()
    case "list_reminders":
      return await listReminders(input)
    case "create_reminder":
      return try createReminder(input)
    case "update_reminder":
      return try updateReminder(input)
    case "complete_reminder":
      return try completeReminder(input)
    case "delete_reminder":
      return try deleteReminder(input)
    default:
      throw NativeError.invalidInput
    }
  }

  private func listLists() -> [String: Any] {
    let defaultID = store.defaultCalendarForNewReminders()?.calendarIdentifier
    let lists = store.calendars(for: .reminder).map { calendar in
      [
        "id": calendar.calendarIdentifier,
        "name": calendar.title,
        "is_default": calendar.calendarIdentifier == defaultID,
      ] as [String: Any]
    }
    return ["lists": lists]
  }

  private func listReminders(_ input: [String: Any]) async -> [String: Any] {
    let includeCompleted = input["include_completed"] as? Bool ?? false
    let calendars = matchingCalendars(input["list_name"] as? String)
    var reminders: [EKReminder] = []

    let incompletePredicate = store.predicateForIncompleteReminders(
      withDueDateStarting: nil,
      ending: nil,
      calendars: calendars
    )
    if let incomplete = await fetch(incompletePredicate) {
      reminders.append(contentsOf: incomplete)
    }

    if includeCompleted {
      let completedPredicate = store.predicateForCompletedReminders(
        withCompletionDateStarting: nil,
        ending: nil,
        calendars: calendars
      )
      if let completed = await fetch(completedPredicate) {
        reminders.append(contentsOf: completed)
      }
    }

    let query = (input["query"] as? String ?? "").lowercased()
    let filtered = reminders
      .map(reminderData)
      .filter { reminder in
        query.isEmpty ||
          (reminder["name"] as? String ?? "").lowercased().contains(query) ||
          (reminder["notes"] as? String ?? "").lowercased().contains(query)
      }
      .sorted { left, right in
        (left["due_date"] as? String ?? "9999") < (right["due_date"] as? String ?? "9999")
      }

    let requestedLimit = input["limit"] as? Int ?? 200
    let limit = min(max(requestedLimit, 1), 500)
    return ["reminders": Array(filtered.prefix(limit))]
  }

  private func createReminder(_ input: [String: Any]) throws -> [String: Any] {
    guard let name = input["name"] as? String, !name.isEmpty else {
      throw NativeError.invalidInput
    }
    let reminder = EKReminder(eventStore: store)
    reminder.title = name
    reminder.notes = input["notes"] as? String
    reminder.calendar = try calendarForInput(input)
    try applyDueDate(input["due_date"] as? String, to: reminder)
    applyPriority(input["priority"] as? Int, to: reminder)

    do {
      try store.save(reminder, commit: true)
    } catch {
      throw NativeError.saveFailed
    }
    return ["reminder": reminderData(reminder)]
  }

  private func updateReminder(_ input: [String: Any]) throws -> [String: Any] {
    let reminder = try findReminder(input)
    if let newName = input["new_name"] as? String { reminder.title = newName }
    if let notes = input["notes"] as? String { reminder.notes = notes }
    if input.keys.contains("due_date") {
      try applyDueDate(input["due_date"] as? String, to: reminder)
    }
    if let priority = input["priority"] as? Int { applyPriority(priority, to: reminder) }
    try save(reminder)
    return ["reminder": reminderData(reminder)]
  }

  private func completeReminder(_ input: [String: Any]) throws -> [String: Any] {
    let reminder = try findReminder(input)
    reminder.isCompleted = true
    try save(reminder)
    return ["reminder": reminderData(reminder)]
  }

  private func deleteReminder(_ input: [String: Any]) throws -> [String: Any] {
    let reminder = try findReminder(input)
    let name = reminder.title ?? ""
    let listName = reminder.calendar?.title ?? ""
    do {
      try store.remove(reminder, commit: true)
    } catch {
      throw NativeError.saveFailed
    }
    return ["deleted": true, "name": name, "list_name": listName]
  }

  private func save(_ reminder: EKReminder) throws {
    do {
      try store.save(reminder, commit: true)
    } catch {
      throw NativeError.saveFailed
    }
  }

  private func matchingCalendars(_ listName: String?) -> [EKCalendar]? {
    guard let listName, !listName.isEmpty else { return nil }
    return store.calendars(for: .reminder).filter { $0.title == listName }
  }

  private func calendarForInput(_ input: [String: Any]) throws -> EKCalendar {
    let calendars = store.calendars(for: .reminder)
    if let listID = input["list_id"] as? String,
      let calendar = calendars.first(where: { $0.calendarIdentifier == listID })
    {
      return calendar
    }
    if let listName = input["list_name"] as? String,
      let calendar = calendars.first(where: { $0.title == listName })
    {
      return calendar
    }
    guard let calendar = store.defaultCalendarForNewReminders() else {
      throw NativeError.listNotFound(input["list_name"] as? String ?? "default")
    }
    return calendar
  }

  private func findReminder(_ input: [String: Any]) throws -> EKReminder {
    let requestedID = input["reminder_id"] as? String
    let requestedName = input["name"] as? String
    let listName = input["list_name"] as? String
    let calendars = matchingCalendars(listName)
    let predicate = store.predicateForIncompleteReminders(
      withDueDateStarting: nil,
      ending: nil,
      calendars: calendars
    )
    let semaphore = DispatchSemaphore(value: 0)
    var result: [EKReminder] = []
    store.fetchReminders(matching: predicate) { reminders in
      result = reminders ?? []
      semaphore.signal()
    }
    semaphore.wait()

    if let match = result.first(where: { reminder in
      if let requestedID { return reminder.calendarItemIdentifier == requestedID }
      return reminder.title == requestedName
    }) {
      return match
    }
    throw NativeError.reminderNotFound(requestedID ?? requestedName ?? "")
  }

  private func applyPriority(_ value: Int?, to reminder: EKReminder) {
    guard let value else { return }
    reminder.priority = value
  }

  private func applyDueDate(_ value: String?, to reminder: EKReminder) throws {
    removeTimeBasedAlarms(from: reminder)
    guard let value, !value.isEmpty else {
      reminder.dueDateComponents = nil
      return
    }
    if value.contains("T"), let date = parseISODate(value) {
      reminder.dueDateComponents = Calendar.current.dateComponents(
        [.year, .month, .day, .hour, .minute, .second], from: date
      )
      reminder.addAlarm(EKAlarm(absoluteDate: date))
      return
    }
    if let date = parseDateOnly(value) {
      reminder.dueDateComponents = Calendar.current.dateComponents([.year, .month, .day], from: date)
      return
    }
    throw NativeError.invalidDate(value)
  }

  private func reminderData(_ reminder: EKReminder) -> [String: Any] {
    var dueDate = ""
    if let components = reminder.dueDateComponents,
      let date = Calendar.current.date(from: components)
    {
      let hasTime = components.hour != nil && components.minute != nil
      dueDate = hasTime ? isoFormatter.string(from: date) : dateOnlyFormatter.string(from: date)
    }
    return [
      "id": reminder.calendarItemIdentifier,
      "name": reminder.title ?? "",
      "list_name": reminder.calendar?.title ?? "",
      "completed": reminder.isCompleted,
      "notes": reminder.notes ?? "",
      "due_date": dueDate.isEmpty ? NSNull() : dueDate,
      "priority": Int(reminder.priority),
    ]
  }

  private func removeTimeBasedAlarms(from reminder: EKReminder) {
    for alarm in reminder.alarms ?? [] where alarm.structuredLocation == nil {
      reminder.removeAlarm(alarm)
    }
  }

  private func fetch(_ predicate: NSPredicate) async -> [EKReminder]? {
    await withCheckedContinuation { continuation in
      store.fetchReminders(matching: predicate) { reminders in
        continuation.resume(returning: reminders)
      }
    }
  }
}

let isoFormatter: ISO8601DateFormatter = {
  let formatter = ISO8601DateFormatter()
  formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
  return formatter
}()

let dateOnlyFormatter: DateFormatter = {
  let formatter = DateFormatter()
  formatter.locale = Locale(identifier: "en_US_POSIX")
  formatter.dateFormat = "yyyy-MM-dd"
  return formatter
}()

func parseISODate(_ value: String) -> Date? {
  if let date = isoFormatter.date(from: value) { return date }
  let fallback = ISO8601DateFormatter()
  fallback.formatOptions = [.withInternetDateTime]
  return fallback.date(from: value)
}

func parseDateOnly(_ value: String) -> Date? {
  dateOnlyFormatter.date(from: value)
}

@main
struct Main {
  static func main() async {
    do {
      guard CommandLine.arguments.count > 1,
        let data = CommandLine.arguments[1].data(using: .utf8),
        let input = try JSONSerialization.jsonObject(with: data) as? [String: Any]
      else { throw NativeError.invalidInput }

      let output = try await NativeHelper().run(input: input)
      let outputData = try JSONSerialization.data(withJSONObject: output, options: [])
      FileHandle.standardOutput.write(outputData)
      FileHandle.standardOutput.write(Data([10]))
    } catch {
      let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
      let output: [String: Any] = ["error": message]
      if let data = try? JSONSerialization.data(withJSONObject: output, options: []) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
      }
      exit(1)
    }
  }
}
